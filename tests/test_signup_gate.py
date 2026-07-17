"""2단계 입구 차단 + 학생 가입 연령 분기 — 기관/교사 접수 종료(410), 만 14세 미만 보호자 동의."""

from datetime import datetime, timedelta

from app.core.security import sha256_hash
from app.models import Consent, EmailVerificationCode, OrgRegistrationRequest, StudentProfile

from tests.conftest import get_email_code


def _code_for(db, email: str, purpose: str) -> str:
    """purpose 지정 코드 삽입 — conftest.get_email_code는 signup 고정이라 보호자용은 별도."""
    code = "123456"
    db.add(
        EmailVerificationCode(
            email=email.strip().lower(),  # 실제 발송 경로(send_email_code)와 동일 정규화
            purpose=purpose,
            code_hash=sha256_hash(code),
            expires_at=datetime.now() + timedelta(minutes=5),  # 앱과 동일 로컬(KST) 규약
        )
    )
    db.commit()
    return code


def _register_student(client, db, email, birth_date, *, guardian_email=None, guardian_code=False):
    body = {
        "name": "가입자",
        "email": email,
        "email_code": get_email_code(db, email),
        "password": "Password123!",
        "birth_date": birth_date,
    }
    if guardian_email is not None:
        body["guardian_email"] = guardian_email
    if guardian_code:
        body["guardian_email_code"] = _code_for(db, guardian_email, "guardian")
    return client.post("/api/v1/auth/register/student", json=body)


def _iso_years_ago(years: int, *, day_offset: int = 0) -> str:
    today = datetime.now().date()
    try:
        d = today.replace(year=today.year - years)
    except ValueError:  # 2/29 태생 보정
        d = today.replace(year=today.year - years, day=today.day - 1)
    return (d + timedelta(days=day_offset)).isoformat()


# ---------------------------------------------------------------- 입구 차단 (은퇴)
def test_org_registration_retired(client, db):
    """기관(학교) 신규 등록 접수 종료 — 410, 신청서·계정이 생기지 않는다."""
    res = client.post(
        "/api/v1/auth/register/org",
        json={
            "org_name": "새학교",
            "contact_name": "김담당",
            "contact_email": "neworg@test.dev",
            "contact_phone": "010-0000-0000",
            "address": "서울",
        },
    )
    assert res.status_code == 410, res.text
    assert db.query(OrgRegistrationRequest).count() == 0


# ---------------------------------------------------------------- 연령 분기
def test_adult_signup_passes_without_guardian(client, db):
    """성인(만 14세 이상)은 보호자 동의 없이 가입 — 생년월일 저장, 동의 행 없음."""
    res = _register_student(client, db, "adult1@test.dev", "1990-01-05")
    assert res.status_code == 200, res.text
    st = db.query(StudentProfile).filter(
        StudentProfile.student_login_id == "adult1@test.dev"
    ).first()
    assert st is not None and st.birth_date.isoformat() == "1990-01-05"
    assert st.guardian_email is None
    assert db.query(Consent).filter(Consent.student_id == st.id).count() == 0


def test_minor_signup_requires_guardian(client, db):
    """만 14세 미만 + 보호자 정보 없음 → 400, 계정 미생성."""
    res = _register_student(client, db, "kid1@test.dev", _iso_years_ago(10))
    assert res.status_code == 400
    assert "보호자" in res.json()["detail"]
    assert (
        db.query(StudentProfile)
        .filter(StudentProfile.student_login_id == "kid1@test.dev")
        .first()
        is None
    )


def test_minor_signup_with_guardian_consent(client, db):
    """만 14세 미만 + 보호자 코드 인증 → 가입 완료, guardian_email·Consent(signup_guardian) 증빙."""
    res = _register_student(
        client, db, "kid2@test.dev", _iso_years_ago(9),
        guardian_email="Mom@Test.dev", guardian_code=True,
    )
    assert res.status_code == 200, res.text
    st = db.query(StudentProfile).filter(
        StudentProfile.student_login_id == "kid2@test.dev"
    ).first()
    assert st.guardian_email == "mom@test.dev"  # 소문자 정규화
    consent = db.query(Consent).filter(Consent.student_id == st.id).one()
    assert consent.consent_type == "signup_guardian"
    assert consent.granted_by_user_id is None and consent.organization_id is None
    assert consent.withdrawn_at is None

    # 같은 보호자 코드 재사용 불가(1회 소비)
    body = {
        "name": "동생",
        "email": "kid3@test.dev",
        "email_code": get_email_code(db, "kid3@test.dev"),
        "password": "Password123!",
        "birth_date": _iso_years_ago(9),
        "guardian_email": "mom@test.dev",
        "guardian_email_code": "123456",  # kid2 가입에서 이미 소비된 코드
    }
    res2 = client.post("/api/v1/auth/register/student", json=body)
    assert res2.status_code == 400


def test_minor_guardian_same_email_rejected(client, db):
    """보호자 이메일 = 본인 이메일이면 거부(자기 동의 방지)."""
    res = _register_student(
        client, db, "kid4@test.dev", _iso_years_ago(8),
        guardian_email="kid4@test.dev", guardian_code=True,
    )
    assert res.status_code == 400
    assert "달라야" in res.json()["detail"]


def test_birth_date_future_rejected(client, db):
    res = _register_student(client, db, "future@test.dev", _iso_years_ago(-1))
    assert res.status_code == 400


def test_fourteenth_birthday_today_is_adult(client, db):
    """경계: 오늘이 만 14세 생일 — 성인 경로(보호자 불필요)."""
    res = _register_student(client, db, "just14@test.dev", _iso_years_ago(14))
    assert res.status_code == 200, res.text


def test_thirteen_years_old_is_minor(client, db):
    """경계: 내일이 14세 생일(오늘 13세) — 보호자 필요."""
    res = _register_student(client, db, "almost14@test.dev", _iso_years_ago(14, day_offset=1))
    assert res.status_code == 400
    assert "보호자" in res.json()["detail"]


def test_signup_guardian_consent_not_counted_as_link_consent(client, db, seed_org):
    """skeptic 실증 회귀(2026-07-17): 기관 코드 경유 미성년 가입의 signup_guardian 동의가
    기관 '보호자 동의(연결) 완료율'에 잡히면 연결 0건인데 100%가 된다 — 지표는
    personal_info(학부모 연결 동의)만 세야 한다(#58 허위지표 부활 방지)."""
    from tests.test_teacher_invite import _make_org_admin, _login, auth as _auth

    org = seed_org["org"]
    body = {
        "name": "기관경유아동",
        "organization_id": org.id,
        "org_code": "TS-EDU-1000",
        "email": "orgkid@test.dev",
        "email_code": get_email_code(db, "orgkid@test.dev"),
        "password": "Password123!",
        "student_login_id": "orgkid01",
        "birth_date": _iso_years_ago(9),
        "guardian_email": "orgmom@test.dev",
        "guardian_email_code": _code_for(db, "orgmom@test.dev", "guardian"),
    }
    assert client.post("/api/v1/auth/register/student", json=body).status_code == 200

    _make_org_admin(db, seed_org)
    tok = _login(client, "principal@test.dev")
    stats = client.get(f"/api/v1/orgs/{org.id}/security-stats", headers=_auth(tok)).json()
    # signup_guardian 동의는 있어도 '연결 동의'는 0건 — 100% 허위가 아니어야 한다
    assert stats["consented"] == 0, stats


def test_guardian_email_send_allows_registered_account(client, db, seed_org):
    """보호자 코드 발송은 기존 계정 이메일(학부모 등)도 허용 — for_account 중복검사 미적용."""
    res = client.post(
        "/api/v1/auth/email/send",
        json={"email": "t1@test.dev", "purpose": "guardian"},
    )
    assert res.status_code == 200, res.text
