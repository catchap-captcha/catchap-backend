from app.services.auth_service import CAPTCHA_DECAY_SECONDS, CAPTCHA_FAIL_THRESHOLD
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


def test_captcha_required_after_threshold_fails(client, seed_org):
    """임계 횟수 이상 연속 실패 → captcha_required, 성공하면 리셋.

    임계값을 하드코딩하지 않고 상수를 따른다 — 5회는 오타 몇 번에도 걸릴 만큼 빡빡해
    8회로 올렸는데(CAPTCHA_FAIL_THRESHOLD), 그때 이 테스트가 상수와 어긋나 깨졌다.
    """
    for i in range(1, CAPTCHA_FAIL_THRESHOLD):
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
    for _ in range(CAPTCHA_FAIL_THRESHOLD):
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


def test_login_role_mismatch_rejected(client, seed_org):
    """탭 구분과 계정 역할 불일치는 403 — 학부모 탭에서 교사 계정이 교사로 로그인되던
    구 동작(role 무시)을 0713 제품 결정으로 뒤집음. 역할 위조는 여전히 불가(토큰 역할=계정 역할)."""
    res = login(client, "parent", "t1@test.dev", "Password123!")  # t1은 실제로 teacher
    assert res.status_code == 403
    assert "선생님 계정" in res.json()["detail"]
    # 올바른 탭(기관 그룹)으로는 실제 역할 그대로 로그인
    ok = login(client, "org", "t1@test.dev", "Password123!")
    assert ok.status_code == 200
    me = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {ok.json()['access_token']}"},
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


def _add_instructor(db, email="inst@test.dev"):
    from datetime import datetime

    from app.core.security import hash_password
    from app.models import User

    u = User(
        email=email,
        password_hash=hash_password("Password123!"),
        name="강사",
        role="instructor",
        status="active",
        email_verified_at=datetime.utcnow(),
    )
    db.add(u)
    db.commit()
    return u


def test_public_ops_login_allows_ops_and_instructor(client, db, seed_org):
    """공개 로그인 폼(public=true)에서 운영자·강사 둘 다 로그인된다.

    종전 0720 정책은 공개 폼에서 운영자를 분리(401)했으나, 2026-07-26 결정으로 두 role 모두
    어느 진입구로든 이메일+비밀번호가 맞으면 로그인된다(auth_service.ops_login 주석 참고).
    프론트 통합 로그인 폼(/login → /auth/public-login)이 이 동작에 의존한다."""
    _add_ops(db)
    _add_instructor(db)
    # 공개 폼(public=true)에서도 운영자 로그인 성공 — 전용 포털 전용이 아니다
    res = client.post(
        "/api/v1/auth/ops-login",
        json={"email": "ops@test.dev", "password": "Password123!", "public": True},
    )
    assert res.status_code == 200
    me_ops = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {res.json()['access_token']}"},
    )
    assert me_ops.json()["role"] == "ops"
    # 같은 공개 폼에서 강사도 성공
    ok = client.post(
        "/api/v1/auth/ops-login",
        json={"email": "inst@test.dev", "password": "Password123!", "public": True},
    )
    assert ok.status_code == 200
    me = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {ok.json()['access_token']}"},
    )
    assert me.json()["role"] == "instructor"


def test_ops_portal_still_allows_ops(client, db, seed_org):
    """전용 /ops/login(public 미지정/false)에서는 운영자가 그대로 로그인된다(회귀 방지)."""
    _add_ops(db)
    assert ops_login(client, "ops@test.dev", "Password123!").status_code == 200
    res = client.post(
        "/api/v1/auth/ops-login",
        json={"email": "ops@test.dev", "password": "Password123!", "public": False},
    )
    assert res.status_code == 200


def _public_login(client, identifier, password, organization_id=None):
    body = {"student_login_id": identifier, "password": password}
    if organization_id:
        body["organization_id"] = organization_id
    return client.post("/api/v1/auth/public-login", json=body)


def test_public_login_authenticates_student(client, db, seed_org):
    """공개 단일 진입(/auth/public-login) — 학생은 학생 경로로 로그인된다."""
    res = _public_login(client, "stu01", "1234")
    assert res.status_code == 200
    me = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {res.json()['access_token']}"}
    )
    assert me.json()["role"] == "student"


def test_public_login_falls_back_to_instructor(client, db, seed_org):
    """학생이 아닌 이메일이면 서버가 강사로 폴백해 로그인시킨다(프론트 폴백 제거)."""
    _add_instructor(db)
    res = _public_login(client, "inst@test.dev", "Password123!")
    assert res.status_code == 200
    me = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {res.json()['access_token']}"}
    )
    assert me.json()["role"] == "instructor"


def test_public_login_includes_ops(client, db, seed_org):
    """공개 단일 진입(/auth/public-login)도 운영자를 인증한다 (2026-07-26 결정).

    학생이 아닌 이메일이면 ops_login으로 폴백하고, 거기서 ops·instructor 두 role을 모두 허용한다."""
    _add_ops(db)
    res = _public_login(client, "ops@test.dev", "Password123!")
    assert res.status_code == 200
    me = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {res.json()['access_token']}"}
    )
    assert me.json()["role"] == "ops"


def test_public_login_wrong_password_is_401(client, db, seed_org):
    """존재하는 학생이라도 비번이 틀리면 학생 경로 그대로 401(강사로 넘어가지 않음)."""
    assert _public_login(client, "stu01", "wrongpw").status_code == 401


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
    """가입 시 다른 기관에 존재하는 아이디도 409 (기관 경유 가입 경로 유지 확인)"""
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
            "password": "Password123!",  # 이메일 가입 전환과 함께 최소 8자로 통일
            "birth_date": "1990-03-01",  # 연령 분기 도입으로 생년월일 필수(성인)
        },
    )
    assert res.status_code == 409


def test_register_student_email_only_and_login(client, db):
    """학생 이메일 가입 전환(2026-07-16) — 기관 없이 이메일만으로 가입하고,
    그 이메일이 로그인 아이디가 된다. organization_id는 None(무소속)."""
    code = get_email_code(db, "stukid@test.dev")
    res = client.post(
        "/api/v1/auth/register/student",
        json={
            "name": "이메일학생",
            "email": "stukid@test.dev",
            "email_code": code,
            "password": "Password123!",
            "birth_date": "1995-06-15",  # 성인 — 보호자 동의 없이 통과
        },
    )
    assert res.status_code == 200, res.text

    ok = client.post(
        "/api/v1/auth/student-login",
        json={"student_login_id": "stukid@test.dev", "password": "Password123!"},
    )
    assert ok.status_code == 200, ok.text
    me = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {ok.json()['access_token']}"},
    )
    assert me.status_code == 200, me.text
    assert me.json()["role"] == "student"
    assert me.json()["organization_id"] is None
    assert me.json()["student"]["student_login_id"] == "stukid@test.dev"

    # 같은 이메일 재가입은 409 (이메일=아이디 전역 유일)
    code2 = get_email_code(db, "stukid@test.dev")
    dup = client.post(
        "/api/v1/auth/register/student",
        json={
            "name": "중복학생",
            "email": "stukid@test.dev",
            "email_code": code2,
            "password": "Password123!",
            "birth_date": "1995-06-15",
        },
    )
    assert dup.status_code == 409


def test_register_student_email_password_min_8(client, db):
    """학생 비밀번호도 학부모와 동일 8자 미만이면 422"""
    code = get_email_code(db, "shortpw@test.dev")
    res = client.post(
        "/api/v1/auth/register/student",
        json={
            "name": "짧은비번",
            "email": "shortpw@test.dev",
            "email_code": code,
            "password": "1234",
            "birth_date": "1995-06-15",
        },
    )
    assert res.status_code == 422


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


def test_register_parent_retired(client, db):
    """제품 전환(학부모 역할 은퇴): 유효한 코드가 있어도 학부모 신규 가입은 410.

    (종전 test_register_parent_with_email_code — 보호자 동의는 이제 학생 가입
    게이트(guardian_email 코드)가 담당한다. 기존 학부모 로그인은 심층 정리까지 유지.)"""
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
    assert res.status_code == 410
    from app.models import User

    assert db.query(User).filter(User.email == "newparent@test.dev").first() is None


def test_register_parent_wrong_code_still_gone(client, db):
    """은퇴 후에는 코드 유효성과 무관하게 410 — 코드 검증 경로 자체에 도달하지 않는다."""
    res = client.post(
        "/api/v1/auth/register/parent",
        json={
            "name": "학부모",
            "email": "x@test.dev",
            "password": "Password123!",
            "email_code": "000000",
        },
    )
    assert res.status_code == 410


def test_register_teacher_retired(client, db, seed_org):
    """제품 전환(학교 기능 은퇴): 유효한 교사 코드가 있어도 신규 교사 가입은 410.

    (종전 test_register_teacher_claims_code — 클레임 성공 경로는 접수 종료로 은퇴,
    3단계 정리 때 코드와 함께 제거한다.)"""
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
    assert res.status_code == 410
    # 접수 종료 후에는 어떤 계정도 만들어지지 않는다
    from app.models import User

    assert db.query(User).filter(User.email == "newteacher@test.dev").first() is None


# (test_verify_org_code 은퇴 — 기관 코드 검증 엔드포인트가 학교 은퇴로 제거됨.
#  기관 코드 경유 학생 가입의 코드 검증은 register_student 내부 검사로 계속 동작한다.)


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
            # 앱 쓰기경로(auth_service._now = KST)와 같은 규약이어야 "1분 전 만료"가
            # 진짜 1분이다. utcnow면 KST 환경에서 9시간 1분 전이 돼, 만료 로직이
            # 9시간까지 틀려도 이 테스트가 통과한다(판별력 상실).
            expires_at=datetime.now() - timedelta(minutes=1),
        )
    )
    db.commit()
    res = client.post(
        "/api/v1/auth/email/verify",
        json={"email": "expired@test.dev", "code": "999999", "purpose": "signup"},
    )
    assert res.status_code == 400


# --- 캡차 요구 자동 해제(시간 창) ---
def test_captcha_requirement_decays_after_window(client, db, seed_org):
    """★임계를 넘겨도 창(30분)이 지나면 캡차 요구가 자동 해제된다.

    종전엔 fail_count가 성공 로그인 외에는 절대 줄지 않아, 캡차를 풀 수 없는 사용자
    (키보드·스크린리더)에게는 영구 차단이었다. 반대로 하드락(10회)은 15분이면 풀려서
    '더 많이 틀린 사람이 더 빨리 풀리는' 역전까지 있었다.
    """
    from datetime import datetime, timedelta

    from app.models import LoginThrottle

    for _ in range(CAPTCHA_FAIL_THRESHOLD):
        res = login(client, "teacher", "t1@test.dev", "wrong")
    assert res.json()["detail"]["captcha_required"] is True

    # 마지막 실패 시각을 창 밖으로 밀어 놓는다(시간을 기다리지 않고 조건만 재현).
    row = db.query(LoginThrottle).filter(LoginThrottle.identifier == "user:t1@test.dev").one()
    row.updated_at = datetime.now() - timedelta(seconds=CAPTCHA_DECAY_SECONDS + 60)
    db.commit()

    after = login(client, "teacher", "t1@test.dev", "wrong")
    assert after.json()["detail"]["captcha_required"] is False, "창이 지났는데 캡차를 계속 요구"


def test_captcha_requirement_stays_within_window(client, db, seed_org):
    """창 안에서는 그대로 요구한다 — 감쇠가 방어를 통째로 없애면 안 된다."""
    from datetime import datetime, timedelta

    from app.models import LoginThrottle

    for _ in range(CAPTCHA_FAIL_THRESHOLD):
        login(client, "teacher", "t1@test.dev", "wrong")
    row = db.query(LoginThrottle).filter(LoginThrottle.identifier == "user:t1@test.dev").one()
    row.updated_at = datetime.now() - timedelta(seconds=CAPTCHA_DECAY_SECONDS - 120)
    db.commit()

    still = login(client, "teacher", "t1@test.dev", "wrong")
    assert still.json()["detail"]["captcha_required"] is True


# --- 학생 비밀번호 재설정 ---
def _student_reset_code(db, login_id: str) -> str:
    """재설정용 이메일 코드를 직접 심는다(dry-run 발송이라 원문을 꺼낼 수 없다)."""
    from datetime import datetime, timedelta

    from app.core.security import sha256_hash
    from app.models import EmailVerificationCode

    code = "654321"
    db.add(
        EmailVerificationCode(
            email=login_id.lower(),
            purpose="reset",
            code_hash=sha256_hash(code),
            expires_at=datetime.now() + timedelta(minutes=10),
        )
    )
    db.commit()
    return code


def test_student_password_reset_end_to_end(client, db, seed_org):
    """학생은 users에 없어 기존 재설정 흐름을 탈 수 없었다 — 전용 경로로 실제 비번이 바뀌는지."""
    from app.core.security import hash_password
    from app.models import StudentProfile

    # 로그인 아이디가 이메일인 학생(2026-07-16 이후 가입 형태)
    st = StudentProfile(
        student_login_id="kid@test.dev",
        student_code="CAT-7777",
        password_hash=hash_password("OldPass123!"),
        nickname="이메일학생",
    )
    db.add(st)
    db.commit()

    assert client.post(
        "/api/v1/auth/student-password-reset/request", json={"student_login_id": "kid@test.dev"}
    ).status_code == 200

    code = _student_reset_code(db, "kid@test.dev")
    res = client.post(
        "/api/v1/auth/student-password-reset/confirm",
        json={"student_login_id": "kid@test.dev", "code": code, "new_password": "NewPass123!"},
    )
    assert res.status_code == 200, res.text

    # 새 비번으로 로그인되고 옛 비번은 막힌다
    ok = client.post(
        "/api/v1/auth/student-login",
        json={"student_login_id": "kid@test.dev", "password": "NewPass123!"},
    )
    assert ok.status_code == 200, ok.text
    old = client.post(
        "/api/v1/auth/student-login",
        json={"student_login_id": "kid@test.dev", "password": "OldPass123!"},
    )
    assert old.status_code == 401


def test_student_password_reset_clears_login_lock(client, db, seed_org):
    """★재설정을 마쳤는데 캡차 게이트에 막히면 의미가 없다 — 로그인 카운터도 함께 풀려야 한다."""
    from app.core.security import hash_password
    from app.models import LoginThrottle, StudentProfile

    st = StudentProfile(
        student_login_id="locked@test.dev",
        student_code="CAT-7778",
        password_hash=hash_password("OldPass123!"),
        nickname="잠긴학생",
    )
    db.add(st)
    db.commit()

    for _ in range(CAPTCHA_FAIL_THRESHOLD):
        res = client.post(
            "/api/v1/auth/student-login",
            json={"student_login_id": "locked@test.dev", "password": "wrong"},
        )
    assert res.json()["detail"]["captcha_required"] is True

    code = _student_reset_code(db, "locked@test.dev")
    assert client.post(
        "/api/v1/auth/student-password-reset/confirm",
        json={"student_login_id": "locked@test.dev", "code": code, "new_password": "NewPass123!"},
    ).status_code == 200

    row = (
        db.query(LoginThrottle)
        .filter(LoginThrottle.identifier == "student:locked@test.dev")
        .first()
    )
    assert row is None or row.fail_count == 0, "재설정 후에도 로그인 카운터가 남아 캡차에 막힌다"
    ok = client.post(
        "/api/v1/auth/student-login",
        json={"student_login_id": "locked@test.dev", "password": "NewPass123!"},
    )
    assert ok.status_code == 200, ok.text


def test_student_password_reset_does_not_leak_existence(client, db, seed_org):
    """없는 아이디·이메일 아닌 아이디로 요청해도 200 — 응답으로 가입 여부를 알 수 없어야 한다."""
    for login_id in ("nobody@test.dev", "stu01"):
        res = client.post(
            "/api/v1/auth/student-password-reset/request", json={"student_login_id": login_id}
        )
        assert res.status_code == 200, f"{login_id} 응답이 200이 아님 → 존재 여부 노출"


def test_student_password_reset_rejects_wrong_code(client, db, seed_org):
    from app.core.security import hash_password
    from app.models import StudentProfile

    db.add(
        StudentProfile(
            student_login_id="wrongcode@test.dev",
            student_code="CAT-7779",
            password_hash=hash_password("OldPass123!"),
            nickname="코드틀림",
        )
    )
    db.commit()
    _student_reset_code(db, "wrongcode@test.dev")
    res = client.post(
        "/api/v1/auth/student-password-reset/confirm",
        json={"student_login_id": "wrongcode@test.dev", "code": "000000", "new_password": "NewPass123!"},
    )
    assert res.status_code == 400


# --- 비밀번호 재설정(전원) ---
def test_password_reset_request_does_not_500(client, db, seed_org):
    """★회귀: password_reset_request 정의가 빠져 있어 프로덕션에서 모든 요청이 500이었다.

    존재하는 계정·없는 계정 모두 200이어야 한다(계정 열거 방지 + 500 재발 방지).
    """
    for email in ("t1@test.dev", "nobody@test.dev"):
        res = client.post("/api/v1/auth/password-reset/request", json={"email": email})
        assert res.status_code == 200, f"{email} → {res.status_code} {res.text}"


def test_password_reset_request_covers_student(client, db, seed_org):
    """학생(users에 없음)도 같은 입구에서 코드를 받는다 — 사용자가 역할을 구분할 필요가 없다."""
    from app.core.security import hash_password
    from app.models import EmailVerificationCode, StudentProfile

    db.add(
        StudentProfile(
            student_login_id="unified@test.dev",
            student_code="CAT-7780",
            password_hash=hash_password("OldPass123!"),
            nickname="통합입구",
        )
    )
    db.commit()
    assert client.post(
        "/api/v1/auth/password-reset/request", json={"email": "unified@test.dev"}
    ).status_code == 200
    issued = (
        db.query(EmailVerificationCode)
        .filter(
            EmailVerificationCode.email == "unified@test.dev",
            EmailVerificationCode.purpose == "reset",
        )
        .count()
    )
    assert issued == 1, "학생인데 재설정 코드가 발급되지 않았다"


def test_password_reset_confirm_falls_through_to_student(client, db, seed_org):
    """같은 입구에서 확정도 되어야 한다 — 학생이면 학생 비번이 바뀐다."""
    from app.core.security import hash_password
    from app.models import StudentProfile

    db.add(
        StudentProfile(
            student_login_id="unified2@test.dev",
            student_code="CAT-7781",
            password_hash=hash_password("OldPass123!"),
            nickname="통합확정",
        )
    )
    db.commit()
    code = _student_reset_code(db, "unified2@test.dev")
    res = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"email": "unified2@test.dev", "code": code, "new_password": "NewPass123!"},
    )
    assert res.status_code == 200, res.text
    ok = client.post(
        "/api/v1/auth/student-login",
        json={"student_login_id": "unified2@test.dev", "password": "NewPass123!"},
    )
    assert ok.status_code == 200, ok.text


# ---- CatChap Guard(성원·민서 캡차) 로그인 경로 -------------------------------
#
# 프론트가 Guard 를 쓸 때만 captcha_session_id 를 함께 보낸다. 그 값이 있으면 우리가
# 발급한 토큰이 아니라 캡차 서버에 물어봐야 한다. 아래 셋을 고정한다.
#   ① session_id 가 없으면 기존 경로 그대로다 (플래그 꺼짐 = 지금 동작)
#   ② session_id 가 있으면 캡차 서버에 묻고, 통과면 로그인이 진행된다
#   ③ 설정이 없으면 500 이다 — 401 로 흘리면 사용자가 캡차를 계속 풀어도 못 들어간다


def _fail_to_captcha_gate(client, email="t1@test.dev"):
    """캡차가 요구되는 상태까지 실패시킨다."""
    for _ in range(CAPTCHA_FAIL_THRESHOLD):
        login(client, "teacher", email, "wrong-password")


def test_guard_토큰은_캡차서버에_물어본다(client, seed_org, monkeypatch):
    from app.clients import main_captcha_client

    seen = {}

    def fake_verify(*, token, session_id, lecture_id=None, purpose="lecture"):
        seen.update(token=token, session_id=session_id, lecture_id=lecture_id, purpose=purpose)
        return True

    monkeypatch.setattr(main_captcha_client, "verify_token", fake_verify)
    _fail_to_captcha_gate(client)

    res = client.post("/api/v1/auth/login", json={
        "role": "teacher", "email": "t1@test.dev", "password": "Password123!",
        "captcha_token": "guard-token", "captcha_session_id": "guard-sess-1234",
        "captcha_purpose": "login",
    })
    assert res.status_code == 200, res.text
    assert seen["session_id"] == "guard-sess-1234"
    # purpose 는 발급 때 값과 같아야 한다. 로그인은 login 이다.
    assert seen["purpose"] == "login"
    # 로그인에는 강의가 없다. 실어 보내면 발급 때 없던 값과 대조하다 불일치로 떨어진다.
    assert seen["lecture_id"] is None


def test_guard_설정이_없으면_500이다(client, seed_org, monkeypatch):
    from app.clients import main_captcha_client

    def boom(**_):
        raise main_captcha_client.MainCaptchaNotConfiguredError("no secret")

    monkeypatch.setattr(main_captcha_client, "verify_token", boom)
    _fail_to_captcha_gate(client)

    res = client.post("/api/v1/auth/login", json={
        "role": "teacher", "email": "t1@test.dev", "password": "Password123!",
        "captcha_token": "guard-token", "captcha_session_id": "guard-sess-1234",
    })
    # 401 이면 사용자는 캡차를 계속 풀어도 못 들어가는 루프에 빠진다. 우리 설정 잘못이다.
    assert res.status_code == 500, res.text


def test_session_id_가_없으면_기존_경로_그대로다(client, seed_org, monkeypatch):
    from app.clients import main_captcha_client

    def must_not_call(**_):
        raise AssertionError("Guard 경로가 아닌데 캡차 서버를 불렀다")

    monkeypatch.setattr(main_captcha_client, "verify_token", must_not_call)
    _fail_to_captcha_gate(client)

    res = login(client, "teacher", "t1@test.dev", "Password123!", captcha_token=forest_token())
    assert res.status_code == 200, res.text


def test_공개로그인이_이메일_경로로_넘길_때도_guard_값을_들고_간다(client, db, seed_org, monkeypatch):
    """공개 로그인(/auth/public-login)은 이메일이면 ops_login 으로 위임한다. 그때 캡차
    관련 값을 전부 옮겨야 한다 — captcha_token 만 넘기면 session_id 가 사라져 게이트가
    'Guard 토큰이 아니다' 로 판단하고 자체 캡차 토큰으로 검사하다 실패한다. 사용자에게는
    '정답을 맞혀도 캡차가 계속 뜨는' 것으로 보인다(2026-08-12 실측)."""
    from app.clients import main_captcha_client

    seen = {}

    def fake_verify(*, token, session_id, lecture_id=None, purpose="lecture"):
        seen.update(token=token, session_id=session_id, purpose=purpose)
        return True

    monkeypatch.setattr(main_captcha_client, "verify_token", fake_verify)
    _add_instructor(db)  # 학생이 아닌 이메일 → ops_login 위임 경로를 타게 한다
    for _ in range(CAPTCHA_FAIL_THRESHOLD):
        client.post("/api/v1/auth/public-login",
                    json={"student_login_id": "inst@test.dev", "password": "wrong"})

    res = client.post("/api/v1/auth/public-login", json={
        "student_login_id": "inst@test.dev", "password": "Password123!",
        "captcha_token": "guard-token", "captcha_session_id": "guard-sess-9876",
        "captcha_purpose": "login",
    })
    assert res.status_code == 200, res.text
    # 값이 위임 과정에서 사라지지 않았는지가 이 테스트의 전부다.
    assert seen.get("session_id") == "guard-sess-9876"
    assert seen.get("purpose") == "login"
