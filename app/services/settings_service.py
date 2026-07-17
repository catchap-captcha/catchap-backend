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
