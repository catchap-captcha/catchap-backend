"""전역 설정(system_settings) 서비스 — 운영자가 콘솔에서 넣는 AI API 키의 단일 창구.

값은 '항상' Fernet(JWT_SECRET 파생)으로 암호화해 저장한다 — DB 덤프·백업 유출이 곧
키 유출이 되지 않게. 원문은 이 모듈의 get_setting을 거쳐야만 복호되고, 읽기 API는
원문 대신 '설정됨 + 끝 4자리'만 내보낸다(마스킹은 ops 엔드포인트 책임).

키 해석 순서: DB(운영자 입력) → .env(배포 설정). 운영자가 콘솔에서 넣은 값이
정본이고, .env는 부트스트랩/개발 폴백이다. commit은 호출자 책임(audit 규약과 동일).
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import SystemSetting

# 운영자 입력을 허용하는 키 화이트리스트 — 임의 키 저장은 거절한다(오타로 죽은 설정이
# 조용히 쌓이는 것 방지 + 엔드포인트가 노출 범위를 좁게 유지).
AI_SETTING_KEYS = ("anthropic_api_key", "openai_api_key")


def _fernet() -> Fernet:
    # captcha_service와 동일 파생(JWT_SECRET sha256 → urlsafe b64) — 새 시크릿을
    # 늘리지 않는다. JWT_SECRET 교체 시 저장된 키는 복호 불가가 되므로 재입력 필요
    # (읽기 경로는 이를 '미설정'으로 정직하게 보고한다 — 아래 get_setting).
    digest = hashlib.sha256(get_settings().JWT_SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def get_setting(db: Session, key: str) -> str | None:
    """복호된 원문 반환 — 없거나 복호 실패(시크릿 교체)면 None(미설정으로 취급)."""
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if row is None:
        return None
    try:
        return _fernet().decrypt(row.value.encode()).decode()
    except InvalidToken:
        # JWT_SECRET가 바뀌어 복호 불가 — 죽은 암호문을 살아있는 설정처럼 보이게 하지
        # 않는다(미설정 반환 → 콘솔에 '재입력 필요'로 드러남).
        return None


def set_setting(db: Session, key: str, value: str, *, updated_by: str | None) -> None:
    """암호화 저장(upsert). 빈 값은 삭제(=미설정 복귀). commit은 호출자 책임."""
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if not value.strip():
        if row is not None:
            db.delete(row)
        return
    token = _fernet().encrypt(value.strip().encode()).decode()
    if row is None:
        db.add(SystemSetting(key=key, value=token, updated_by=updated_by))
    else:
        row.value = token
        row.updated_by = updated_by


def setting_updated_at(db: Session, key: str):
    """그 설정을 마지막으로 저장한 시각(datetime) — 없으면 None. 원문은 안 읽어 마스킹과 무관하다."""
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    return row.updated_at if row is not None else None


def masked_status(db: Session, key: str, env_fallback: str = "") -> dict:
    """읽기 API용 상태 — 원문 미반환. {configured, last4, source, updated_at}."""
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if row is not None:
        plain = get_setting(db, key)
        if plain:
            return {
                "configured": True,
                "last4": plain[-4:],
                "source": "console",
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
        # 행은 있는데 복호 불가(시크릿 교체) — 재입력을 요구하는 정직한 상태
        return {"configured": False, "last4": None, "source": "stale", "updated_at": None}
    env_val = (env_fallback or "").strip()
    if env_val:
        return {"configured": True, "last4": env_val[-4:], "source": "env", "updated_at": None}
    return {"configured": False, "last4": None, "source": None, "updated_at": None}


def resolve_anthropic_key(db: Session) -> str:
    """LLM(Anthropic) 키 — DB(콘솔) 우선, .env 폴백. 없으면 빈 문자열."""
    return get_setting(db, "anthropic_api_key") or (get_settings().ANTHROPIC_API_KEY or "").strip()


def resolve_openai_key(db: Session) -> str:
    """STT(OpenAI Whisper) 키 — DB(콘솔) 우선, .env 폴백. 없으면 빈 문자열."""
    return get_setting(db, "openai_api_key") or (get_settings().OPENAI_API_KEY or "").strip()


# ── 문항 생성 '출제 규칙'을 (강사 계정 × 코스 과목)별로 두기 ──────────────────────────
# 전역 llm_gen_rules 하나 대신, 강사·과목 조합마다 전용 규칙을 둔다. 새 테이블 없이 같은
# system_settings 에 복합 키로 넣어 마이그레이션이 없다(값은 다른 설정처럼 암호화 저장).
# instructor_id 는 UUID, subject 는 '수학'·'일반' 등 구분자('::')가 없어 키 파싱이 안전하다.
GEN_RULES_KEY = "llm_gen_rules"


def scoped_gen_key(instructor_id: str, subject: str) -> str:
    return f"{GEN_RULES_KEY}::i:{instructor_id}::s:{subject}"


def resolve_gen_rules(db: Session, instructor_id: str | None, subject: str | None) -> str | None:
    """생성 출제 규칙 해석 — (강사+과목) 전용 → 전역(llm_gen_rules) 순으로 첫 비어있지 않은 값.
    둘 다 없으면 None(호출부가 서버 기본값 DEFAULT_GEN_RULES 를 쓴다)."""
    if instructor_id and subject:
        scoped = get_setting(db, scoped_gen_key(instructor_id, subject))
        if scoped and scoped.strip():
            return scoped
    glob = get_setting(db, GEN_RULES_KEY)
    return glob if glob and glob.strip() else None


def list_scoped_gen_rules(db: Session) -> list[dict]:
    """저장된 (강사, 과목)별 출제 규칙 전부 — [{instructor_id, subject, rules}]. 키 접두사로 찾아 복호."""
    prefix = f"{GEN_RULES_KEY}::i:"
    out: list[dict] = []
    for row in db.query(SystemSetting).filter(SystemSetting.key.like(prefix + "%")).all():
        rest = row.key[len(prefix):]
        if "::s:" not in rest:
            continue
        iid, subject = rest.split("::s:", 1)
        rules = get_setting(db, row.key)  # 복호
        if rules and rules.strip():
            out.append({"instructor_id": iid, "subject": subject, "rules": rules})
    return out
