"""엔드포인트 공용 헬퍼 (감사 로그, 상태 라벨, 날짜 라벨)."""

from datetime import date, datetime

from sqlalchemy.orm import Session

from app.models import AuditLog

# StudentProfile.status <-> 화면 한글 라벨
STATUS_LABEL = {"good": "좋음", "inactive": "학습 뜸함", "needs_help": "도움 필요"}
STATUS_KEY = {v: k for k, v in STATUS_LABEL.items()}


def status_label(status: str) -> str:
    return STATUS_LABEL.get(status, status)


def status_key(label: str) -> str:
    return STATUS_KEY.get(label, label)


def audit(
    db: Session,
    *,
    action: str,
    actor_user_id: str | None = None,
    organization_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            before_json=before,
            after_json=after,
        )
    )


def date_label(d: date | datetime | None) -> str:
    """오늘/어제/N일 전 라벨 (화면 표기용)."""
    if d is None:
        return ""
    if isinstance(d, datetime):
        d = d.date()
    days = (date.today() - d).days
    if days <= 0:
        return "오늘"
    if days == 1:
        return "어제"
    return f"{days}일 전"
