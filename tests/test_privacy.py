"""개인정보 파기(익명화) — 탈퇴 엔드포인트 + 보존만료 배치 (적대적검토 Group B #59)."""

from datetime import datetime, timedelta

from app.core.security import hash_password
from app.models import StudentProfile, User


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def _org_admin(db, org):
    admin = User(
        email="principal@test.dev", password_hash=hash_password("Password123!"),
        name="교장", role="org_admin", organization_id=org.id,
        email_verified_at=datetime.utcnow(),
    )
    db.add(admin)
    db.commit()
    return admin


def test_anonymize_student_purges_pii_idempotent(db, seed_org):
    from app.services.privacy_service import anonymize_student

    s = seed_org["student"]
    s.real_name, s.age, s.gender = "김실명", 8, "female"
    db.commit()

    assert anonymize_student(db, s) is True
    db.commit()
    db.refresh(s)
    assert s.real_name is None and s.age is None and s.gender is None
    assert s.nickname == "탈퇴한 학생"
    assert s.status == "disabled" and s.password_hash == ""
    assert s.student_login_id.startswith("del_") and s.class_id is None
    # 멱등 — 이미 파기된 계정은 변경 없음
    assert anonymize_student(db, s) is False


def test_retention_batch_anonymizes_only_stale(db, seed_org):
    from app.services.privacy_service import anonymize_stale_students

    s = seed_org["student"]
    s.real_name = "김실명"
    s.status = "disabled"
    s.last_login_at = datetime.now() - timedelta(days=400)  # 1년 넘게 비활성
    db.commit()

    n = anonymize_stale_students(db, inactive_days=365)
    assert n == 1
    db.refresh(s)
    assert s.real_name is None  # 파기됨

    # 최근 로그인/활성 학생은 대상 아님
    s2 = StudentProfile(
        organization_id=seed_org["org"].id, class_id=None, student_login_id="recent",
        student_code="CAT-9999", password_hash=hash_password("1234"), nickname="최근",
        real_name="살아있는이름", status="disabled", last_login_at=datetime.now(),
    )
    db.add(s2)
    db.commit()
    assert anonymize_stale_students(db, inactive_days=365) == 0
    db.refresh(s2)
    assert s2.real_name == "살아있는이름"  # 미파기


def test_withdraw_endpoint_blocks_login(client, db, seed_org):
    """org_admin 탈퇴 → PII 파기 + 로그인 차단."""
    org = seed_org["org"]
    seed_org["student"].real_name = "김실명"
    db.commit()
    _org_admin(db, org)
    admin_tok = client.post(
        "/api/v1/auth/login", json={"email": "principal@test.dev", "password": "Password123!"}
    ).json()["access_token"]

    r = client.post(
        f"/api/v1/orgs/{org.id}/students/{seed_org['student'].id}/withdraw",
        headers=auth(admin_tok),
    )
    assert r.status_code == 200, r.text
    assert r.json()["anonymized"] is True

    db.expire_all()
    st = db.get(StudentProfile, seed_org["student"].id)
    assert st.real_name is None and st.status == "disabled"

    # 탈퇴 학생은 로그인 불가(원래 아이디 stu01은 이제 유효하지 않음)
    login = client.post(
        "/api/v1/auth/student-login",
        json={"organization_id": org.id, "student_login_id": "stu01", "password": "1234"},
    )
    assert login.status_code != 200
