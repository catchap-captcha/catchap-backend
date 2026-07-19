"""운영자 AI 모델 선택(#26) — 생성/검증 슬롯 해석 + 자동 스왑 + 토큰/추정 비용.

실제 LLM 호출(문항 생성·자기검증)이 '어떤 모델을 쓸지'의 단일 창구. ai_client는 여기서
받은 후보 목록을 순서대로 시도한다(첫 성공에서 멈춤).

자동 스왑(사용자 결정 2026-07-19): 슬롯에 지정된 모델이 Off이거나 호출 실패면 다른 켜진
모델로 자동 대체 — 파이프라인이 통째로 죽지 않게. auto_swap이 꺼져 있으면 슬롯 모델만
쓴다(없으면 후보 0 → 호출자가 .env LLM_MODEL로 폴백).

한계(1단계): 실제 호출은 Anthropic Messages API만 지원한다(ai_client). provider는 표시용
라벨이고, 슬롯에는 Anthropic 계열 model_id를 넣는 걸 전제로 한다. OpenAI 등 타사 LLM을
'생성/검증' 백엔드로 붙이는 건 다음 단계(별도 호출 경로 필요).
"""

from sqlalchemy.orm import Session

from app.models import AiModelConfig
from app.services import settings_service

AUTO_SWAP_KEY = "ai_auto_swap"
SLOTS = ("generate", "verify")
# 슬롯 포인터는 system_settings에 둔다(모델 컬럼 아님) — 같은 모델을 두 슬롯에 함께 쓰기 위해
_SLOT_KEYS = {"generate": "ai_slot_generate", "verify": "ai_slot_verify"}


def auto_swap_enabled(db: Session) -> bool:
    return settings_service.get_setting(db, AUTO_SWAP_KEY) == "1"


def set_auto_swap(db: Session, on: bool, *, updated_by: str | None) -> None:
    settings_service.set_setting(db, AUTO_SWAP_KEY, "1" if on else "0", updated_by=updated_by)


def get_slot(db: Session, role: str) -> str | None:
    """이 슬롯에 배정된 모델 config id(미배정이면 None)."""
    return settings_service.get_setting(db, _SLOT_KEYS[role]) or None


def set_slot(db: Session, role: str, model_id: str | None, *, updated_by: str | None) -> None:
    """슬롯 배정(빈 값=미배정 복귀). commit은 호출자 책임."""
    settings_service.set_setting(db, _SLOT_KEYS[role], model_id or "", updated_by=updated_by)


def resolve_candidates(db: Session, role: str) -> list[AiModelConfig]:
    """이 역할(생성/검증)에 시도할 모델 후보를 우선순위 순으로 — 호출자가 앞에서부터 시도한다.

    ① 슬롯에 지정된 켜진 모델(있으면 최우선) → ② 자동 스왑이 켜져 있으면 나머지 켜진 모델.
    비어 있으면 설정된 게 없다는 뜻(호출자가 .env 폴백). 슬롯 모델이 Off면 ①이 비어 ②로 넘어간다."""
    enabled = (
        db.query(AiModelConfig)
        .filter(AiModelConfig.enabled.is_(True))
        .order_by(AiModelConfig.created_at)
        .all()
    )
    by_id = {m.id: m for m in enabled}
    slot_id = get_slot(db, role)
    slot_model = by_id.get(slot_id) if slot_id else None  # 배정됐어도 Off면 by_id에 없어 None
    if slot_model:
        rest = [m for m in enabled if m.id != slot_model.id] if auto_swap_enabled(db) else []
        return [slot_model, *rest]
    # 슬롯 미배정(또는 슬롯 모델이 Off) — 자동 스왑이면 켜진 모델 아무거나, 아니면 없음
    return enabled if auto_swap_enabled(db) else []


def record_usage(db: Session, config_id: str, tokens_in: int, tokens_out: int) -> None:
    """모델별 누적 토큰 합산 — 추정 비용의 근거. commit은 호출자 책임.

    원자적 UPDATE(SET x = x + n)로 증가한다 — 파이썬에서 읽고-더하고-쓰면 동시 생성
    요청 사이에 lost update가 나 토큰이 새어나간다(검증 1회에 solve 3~4회라 한 요청
    안에서도 여러 번 호출됨). 없는 id는 0행 갱신으로 조용히 무시된다."""
    ti = max(0, int(tokens_in or 0))
    to = max(0, int(tokens_out or 0))
    if not ti and not to:
        return
    db.query(AiModelConfig).filter(AiModelConfig.id == config_id).update(
        {
            AiModelConfig.tokens_in: AiModelConfig.tokens_in + ti,
            AiModelConfig.tokens_out: AiModelConfig.tokens_out + to,
        },
        synchronize_session=False,
    )


def estimate_cost_usd(m: AiModelConfig) -> float:
    """누적 토큰 × 단가($/100만 토큰) — 실비용 아닌 운영자 참고용 추정치."""
    return round(
        (int(m.tokens_in or 0) / 1_000_000) * float(m.cost_in_usd or 0)
        + (int(m.tokens_out or 0) / 1_000_000) * float(m.cost_out_usd or 0),
        4,
    )
