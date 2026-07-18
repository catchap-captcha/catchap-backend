"""아동(만14 미만) 테스트데이터 파기 — 행동데이터 무손실 불변식(사용자 지시 0718).

파기 = anonymize_student 단일 경로(PII 익명화). behavior_summaries는 성인(팀원)
생성분이라 절대 삭제하지 않고 actor_band 태그 그대로 보존·사용한다.
"""

from datetime import date, datetime

from app.core.security import hash_password
from app.models import BehaviorSummary, StudentProfile
from app.services import privacy_service
from app.services.captcha_service import record_behavior_event


def _student(db, seed_org, login_id, **kw):
    st = StudentProfile(
        organization_id=seed_org["org"].id,
        student_login_id=login_id,
        student_code=f"CAT-PG{login_id[-2:].upper()}Z",
        password_hash=hash_password("pw123456"),
        nickname=login_id,
        real_name=f"실명-{login_id}",
        **kw,
    )
    db.add(st)
    db.commit()
    return st


def test_purge_minors_anonymizes_and_preserves_behavior(client, db, seed_org):
    today = date.today()
    minor_birth = _student(db, seed_org, "pg-birth", birth_date=date(today.year - 9, 1, 1))
    minor_age = _student(db, seed_org, "pg-age", age=10)
    adult = _student(db, seed_org, "pg-adult", birth_date=date(1990, 2, 2))
    unknown = _student(db, seed_org, "pg-none")  # 연령 미상 — 건드리면 안 됨

    # 각자 행동데이터 1건씩 적재(파기 후에도 전부 남아야 한다)
    for s in (minor_birth, minor_age, adult, unknown):
        record_behavior_event(
            db, organization_id=seed_org["org"].id, student_id=s.id,
            source_type="game", behavior={"solve_time_ms": 900}, correct=True,
        )
    db.commit()
    before = db.query(BehaviorSummary).count()
    before_bands = sorted(
        b.actor_band for b in db.query(BehaviorSummary).all() if b.actor_band is not None
    )

    # 대상 판별: 확인된 미성년만 (seed_org 기본 학생 stu01은 연령 미상이라 제외돼야 함)
    targets = {s.student_login_id for s in privacy_service.find_minor_students(db)}
    assert {"pg-birth", "pg-age"} <= targets
    assert "pg-adult" not in targets and "pg-none" not in targets

    n = privacy_service.purge_minor_students(db)
    assert n >= 2

    db.expire_all()
    for sid, purged in [(minor_birth.id, True), (minor_age.id, True), (adult.id, False), (unknown.id, False)]:
        st = db.get(StudentProfile, sid)
        if purged:
            assert st.real_name is None and st.status == "disabled"
            assert st.birth_date is None and st.guardian_email is None  # PII 파기
        else:
            assert st.real_name is not None and st.status != "disabled"

    # ★불변식: 행동데이터는 1행도 삭제되지 않고 actor_band도 그대로
    assert db.query(BehaviorSummary).count() == before
    after_bands = sorted(
        b.actor_band for b in db.query(BehaviorSummary).all() if b.actor_band is not None
    )
    assert after_bands == before_bands

    # 파기된 미성년은 로그인 불가
    r = client.post(
        "/api/v1/auth/student-login",
        json={"student_login_id": "pg-birth", "password": "pw123456"},
    )
    assert r.status_code != 200

    # 멱등: 재실행 시 추가 파기 0
    assert privacy_service.purge_minor_students(db) == 0
