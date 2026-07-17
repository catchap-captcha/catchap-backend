"""행동데이터 행위자 연령대 태깅(actor_band) — 아동 파기에서 성인 생성분을 지키는 축.

record_behavior_event는 다섯 수집 표면(game/edu-api/lecture/forest/redteam)의 단일
적재 함수 — 여기서 밴드가 맞으면 전 표면이 맞는다.
"""

from datetime import date, datetime

from app.core.security import hash_password
from app.models import BehaviorSummary, StudentProfile
from app.services.captcha_service import record_behavior_event


def _student(db, seed_org, login_id, **kw):
    st = StudentProfile(
        organization_id=seed_org["org"].id,
        student_login_id=login_id,
        student_code=f"CAT-{login_id[-4:].upper()}X",
        password_hash=hash_password("pw123456"),
        nickname=login_id,
        **kw,
    )
    db.add(st)
    db.commit()
    return st


def _record(db, seed_org, student_id):
    record_behavior_event(
        db,
        organization_id=seed_org["org"].id,
        student_id=student_id,
        source_type="game",
        behavior={"solve_time_ms": 1200},
        correct=True,
    )
    db.commit()
    return (
        db.query(BehaviorSummary)
        .order_by(BehaviorSummary.created_at.desc(), BehaviorSummary.id.desc())
        .first()
    )


def test_adult_by_birth_date(db, seed_org):
    st = _student(db, seed_org, "band-adult", birth_date=date(1992, 2, 2))
    assert _record(db, seed_org, st.id).actor_band == "adult"


def test_minor_by_birth_date(db, seed_org):
    today = datetime.now().date()
    st = _student(db, seed_org, "band-minor", birth_date=date(today.year - 9, 1, 1))
    assert _record(db, seed_org, st.id).actor_band == "minor"


def test_minor_by_legacy_school_age(db, seed_org):
    """구계정: birth_date 없이 학교 입력 age(3~13)만 → 미성년 확정."""
    st = _student(db, seed_org, "band-legacy", age=10)
    assert _record(db, seed_org, st.id).actor_band == "minor"


def test_unknown_when_no_info_or_anonymous(db, seed_org):
    """정보 없음·익명은 NULL(미상) — 'adult'로 추정하지 않는다(보수적 파기 판별)."""
    st = _student(db, seed_org, "band-none")  # birth_date도 age도 없음
    assert _record(db, seed_org, st.id).actor_band is None
    assert _record(db, seed_org, None).actor_band is None  # 익명(외부 임베드 미검증 등)
