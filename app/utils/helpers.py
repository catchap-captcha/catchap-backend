"""엔드포인트 공용 헬퍼 (감사 로그, 상태 라벨, 날짜 라벨)."""

import re
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.models import AuditLog


# ── 앱 전역 '오늘/지금' 기준 ────────────────────────────────────────────
# created_at 등은 로컬 naive(datetime.now)로, 만료류는 UTC naive로 저장되는 이중 규약이
# 남아 있다. "오늘/이번 주" 같은 '날짜 경계 판정'은 반드시 이 헬퍼로 통일해 자정 경계
# 밀림을 막는다.
# ⚠️ 배포 시 컨테이너 TZ=Asia/Seoul 로 고정해야 로컬 날짜 판정이 사용자 시간대와 일치한다
#    (TZ 미고정 시 UTC 자정에 '오늘'이 하루 어긋날 수 있음).
def today() -> date:
    """앱 전역 '오늘' (로컬 날짜). 날짜 경계 판정은 이 함수로 통일한다."""
    return date.today()


def now() -> datetime:
    """앱 전역 '지금' (로컬 시각, created_at 저장 규약과 일치)."""
    return datetime.now()

# 반 이름 맨 앞의 학년 숫자 ("1-2반", "1학년 2반", "3반" → 1/1/3)
_GRADE_RE = re.compile(r"^\s*(\d+)")


def parse_grade(name: str | None) -> int | None:
    """반 이름에서 학년(정수)을 파싱. 못 찾으면 None."""
    if not name:
        return None
    m = _GRADE_RE.match(name)
    return int(m.group(1)) if m else None

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
