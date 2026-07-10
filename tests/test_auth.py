from tests.conftest import get_email_code


def login(client, role, email, password, captcha_token=None):
    payload = {"role": role, "email": email, "password": password}
    if captcha_token is not None:
        payload["captcha_token"] = captcha_token
    return client.post("/api/v1/auth/login", json=payload)


def forest_token():
    """캡차 요구 상태를 통과하기 위한 유효한 메인 캡차(forest) 토큰 — 단일사용."""
    from app.services import forest_captcha as fc

    return fc.service.issue_token()


def test_login_success_and_me(client, db, seed_org):
    res = login(client, "teacher", "t1@test.dev", "Password123!")
    assert res.status_code == 200
    tokens = res.json()
    assert tokens["access_token"] and tokens["refresh_token"]

    me = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["role"] == "teacher"
    assert me.json()["name"] == "테스트교사"


def test_login_wrong_password(client, seed_org):
    assert login(client, "teacher", "t1@test.dev", "wrong").status_code == 401


def test_captcha_required_after_five_fails(client, seed_org):
    """5회 이상 연속 실패 → captcha_required, 성공하면 리셋"""
    for i in range(1, 5):
        res = login(client, "teacher", "t1@test.dev", "wrong")
        assert res.status_code == 401
        assert res.json()["detail"]["captcha_required"] is False, f"{i}번째 실패에서 캡차 요구"

    res5 = login(client, "teacher", "t1@test.dev", "wrong")
    assert res5.status_code == 401
    assert res5.json()["detail"]["captcha_required"] is True

    # 6번째도 계속 요구 — 캡차 토큰 없인 자격 검증 자체가 막힌다(카운트는 안 올라감)
    res6 = login(client, "teacher", "t1@test.dev", "wrong")
    assert res6.json()["detail"]["captcha_required"] is True

    # 캡차 요구 상태에서는 올바른 비밀번호도 토큰 없이는 거부된다 (로그인 게이트)
    blocked = login(client, "teacher", "t1@test.dev", "Password123!")
    assert blocked.status_code == 401
    assert blocked.json()["detail"]["captcha_required"] is True

    # 캡차 통과 토큰과 함께 성공하면 리셋 → 이후 1회 실패는 캡차 불필요
    ok = login(client, "teacher", "t1@test.dev", "Password123!", captcha_token=forest_token())
    assert ok.status_code == 200
    res_after = login(client, "teacher", "t1@test.dev", "wrong")
    assert res_after.json()["detail"]["captcha_required"] is False


def test_student_captcha_counter(client, seed_org):
    """학생 로그인도 실패 카운트/리셋 동작"""
    for _ in range(5):
        res = client.post(
            "/api/v1/auth/student-login",
            json={"student_login_id": "stu01", "password": "wrong"},
        )
    assert res.json()["detail"]["captcha_required"] is True
    # 캡차 요구 상태 — 올바른 비밀번호도 토큰 없이는 거부, 토큰과 함께면 성공(리셋)
    blocked = client.post(
        "/api/v1/auth/student-login",
        json={"student_login_id": "stu01", "password": "1234"},
    )
    assert blocked.status_code == 401
    ok = client.post(
        "/api/v1/auth/student-login",
        json={
            "student_login_id": "stu01",
            "password": "1234",
            "captcha_token": forest_token(),
        },
    )
    assert ok.status_code == 200


def test_login_ignores_declared_role(client, seed_org):
    """역할은 계정에서 판별 — 클라이언트가 보낸 role은 무시하고 실제 역할로 로그인."""
    res = login(client, "parent", "t1@test.dev", "Password123!")  # t1은 실제로 teacher
    assert res.status_code == 200
    me = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {res.json()['access_token']}"},
    )
    assert me.json()["role"] == "teacher"


def _add_ops(db):
    from datetime import datetime

    from app.core.security import hash_password
    from app.models import User

    ops = User(
        email="ops@test.dev",
        password_hash=hash_password("Password123!"),
        name="운영자",
        role="ops",
        status="active",
        email_verified_at=datetime.utcnow(),
    )
    db.add(ops)
    db.commit()
    return ops


def ops_login(client, email, password):
    return client.post(
        "/api/v1/auth/ops-login", json={"email": email, "password": password}
    )


def test_ops_cannot_use_general_login(client, db, seed_org):
    """운영자 계정은 일반 로그인 폼(/auth/login)으로 인증되지 않는다."""
    _add_ops(db)
    assert login(client, "ops", "ops@test.dev", "Password123!").status_code == 401
    # role을 비워도 마찬가지 (계정 역할이 ops면 일반 폼 거부)
    res = client.post(
        "/api/v1/auth/login", json={"email": "ops@test.dev", "password": "Password123!"}
    )
    assert res.status_code == 401


def test_ops_login_success(client, db, seed_org):
    """전용 경로(/auth/ops-login)에서만 운영자 로그인 성공."""
    _add_ops(db)
    res = ops_login(client, "ops@test.dev", "Password123!")
    assert res.status_code == 200
    me = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {res.json()['access_token']}"},
    )
    assert me.json()["role"] == "ops"


def test_ops_login_rejects_non_ops(client, db, seed_org):
    """일반 사용자 계정은 운영자 전용 경로로 토큰을 받을 수 없다."""
    _add_ops(db)
    assert ops_login(client, "t1@test.dev", "Password123!").status_code == 401
    assert ops_login(client, "ops@test.dev", "wrong").status_code == 401


def test_student_login_and_me(client, db, seed_org):
    org = seed_org["org"]
    res = client.post(
        "/api/v1/auth/student-login",
        json={"organization_id": org.id, "student_login_id": "stu01", "password": "1234"},
    )
    assert res.status_code == 200
    token = res.json()["access_token"]
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["role"] == "student"
    assert me.json()["student"]["student_code"] == "CAT-1111"


def test_student_login_without_org(client, seed_org):
    """기관 미지정 로그인 — 아이디로 기관 자동 판별"""
    res = client.post(
        "/api/v1/auth/student-login",
        json={"student_login_id": "stu01", "password": "1234"},
    )
    assert res.status_code == 200


def test_student_id_globally_unique(client, db, seed_org):
    """학생 아이디는 전 기관 전역 유일 — DB 유니크 제약 + 가입 시 409"""
    import pytest
    from sqlalchemy.exc import IntegrityError

    from app.core.security import hash_password
    from app.models import Organization, StudentProfile

    other = Organization(name="다른유치원", code="DK-EDU-0001", org_type="유치원")
    db.add(other)
    db.flush()
    # 다른 기관이라도 같은 login_id는 DB 레벨에서 거부
    db.add(
        StudentProfile(
            organization_id=other.id,
            student_login_id="stu01",
            student_code="CAT-9999",
            password_hash=hash_password("1234"),
            nickname="동명학생",
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_check_student_id_endpoint(client, db, seed_org):
    """가입 화면 '중복 확인' — 사용 중이면 available=False"""
    taken = client.post(
        "/api/v1/auth/check-student-id", json={"student_login_id": "stu01"}
    )
    assert taken.json()["available"] is False
    free = client.post(
        "/api/v1/auth/check-student-id", json={"student_login_id": "brandnew99"}
    )
    assert free.json()["available"] is True
    # 3자 미만은 사용 불가 처리
    short = client.post("/api/v1/auth/check-student-id", json={"student_login_id": "ab"})
    assert short.json()["available"] is False


def test_register_student_rejects_duplicate_id(client, db, seed_org):
    """가입 시 다른 기관에 존재하는 아이디도 409"""
    from tests.conftest import get_email_code

    code = get_email_code(db, "guardian@test.dev")
    res = client.post(
        "/api/v1/auth/register/student",
        json={
            "name": "새학생",
            "organization_id": seed_org["org"].id,
            "org_code": "TS-EDU-1000",
            "email": "guardian@test.dev",
            "email_code": code,
            "student_login_id": "stu01",  # 이미 사용 중
            "password": "1234",
        },
    )
    assert res.status_code == 409


def test_email_send_rejects_registered_account_email(client, db, seed_org):
    """계정용 이메일(for_account)은 이미 가입된 이메일이면 발송 전 409"""
    res = client.post(
        "/api/v1/auth/email/send",
        json={"email": "t1@test.dev", "purpose": "signup", "for_account": True},
    )
    assert res.status_code == 409
    # 학생 가입의 보호자 이메일(for_account=False)은 기존 계정과 무관하게 허용
    res2 = client.post(
        "/api/v1/auth/email/send",
        json={"email": "t1@test.dev", "purpose": "signup", "for_account": False},
    )
    assert res2.status_code == 200


def test_refresh_rotation(client, seed_org):
    tokens = login(client, "teacher", "t1@test.dev", "Password123!").json()
    res = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert res.status_code == 200
    # 회전 후 이전 refresh 재사용 불가
    res2 = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert res2.status_code == 401


def test_register_parent_with_email_code(client, db):
    code = get_email_code(db, "newparent@test.dev")
    res = client.post(
        "/api/v1/auth/register/parent",
        json={
            "name": "새학부모",
            "email": "newparent@test.dev",
            "phone": "010-0000-0000",
            "password": "Password123!",
            "email_code": code,
        },
    )
    assert res.status_code == 200
    # 코드 1회 사용 — 같은 코드 재사용 불가
    res2 = client.post(
        "/api/v1/auth/register/parent",
        json={
            "name": "또학부모",
            "email": "newparent@test.dev",
            "password": "Password123!",
            "email_code": code,
        },
    )
    assert res2.status_code in (400, 409)
    # 가입한 계정 로그인
    assert login(client, "parent", "newparent@test.dev", "Password123!").status_code == 200


def test_register_parent_wrong_code(client, db):
    res = client.post(
        "/api/v1/auth/register/parent",
        json={
            "name": "학부모",
            "email": "x@test.dev",
            "password": "Password123!",
            "email_code": "000000",
        },
    )
    assert res.status_code == 400


def test_register_teacher_claims_code(client, db, seed_org):
    org = seed_org["org"]
    code = get_email_code(db, "newteacher@test.dev")
    res = client.post(
        "/api/v1/auth/register/teacher",
        json={
            "name": "새교사",
            "email": "newteacher@test.dev",
            "password": "Password123!",
            "email_code": code,
            "organization_id": org.id,
            "teacher_code": "T-2222",
        },
    )
    assert res.status_code == 200
    # 이미 클레임된 코드 재사용 불가
    code2 = get_email_code(db, "other@test.dev")
    res2 = client.post(
        "/api/v1/auth/register/teacher",
        json={
            "name": "다른교사",
            "email": "other@test.dev",
            "password": "Password123!",
            "email_code": code2,
            "organization_id": org.id,
            "teacher_code": "T-2222",
        },
    )
    assert res2.status_code == 409


def test_verify_org_code(client, seed_org):
    org = seed_org["org"]
    ok = client.post(
        "/api/v1/auth/verify-org-code",
        json={"organization_id": org.id, "code": "TS-EDU-1000"},
    )
    assert ok.json()["valid"] is True
    bad = client.post(
        "/api/v1/auth/verify-org-code",
        json={"organization_id": org.id, "code": "WRONG-0000"},
    )
    assert bad.json()["valid"] is False


def test_email_verification_expiry(client, db):
    """만료된 코드는 거부"""
    from datetime import datetime, timedelta

    from app.core.security import sha256_hash
    from app.models import EmailVerificationCode

    db.add(
        EmailVerificationCode(
            email="expired@test.dev",
            purpose="signup",
            code_hash=sha256_hash("999999"),
            expires_at=datetime.utcnow() - timedelta(minutes=1),
        )
    )
    db.commit()
    res = client.post(
        "/api/v1/auth/email/verify",
        json={"email": "expired@test.dev", "code": "999999", "purpose": "signup"},
    )
    assert res.status_code == 400
