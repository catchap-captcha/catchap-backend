"""소셜 로그인(카카오·네이버·구글) — provider HTTP 규격 + 계정 연결·가입 판정.

검증 규약:
- 네트워크를 타지 않는다. 어댑터 단위는 httpx.MockTransport(테스트 test_payment_gateways와
  같은 방식), 흐름 단위는 build_provider를 스텁으로 갈아끼운다.
- 이 도메인의 핵심 불변식 3가지를 회귀로 고정한다:
  ① 신규 사용자는 생년월일을 받기 전까지 계정이 만들어지지 않는다(연령 게이트 우회 방지)
  ② 검증되지 않은 이메일로는 기존 계정에 자동 연결되지 않는다(계정 탈취 방지)
  ③ 마지막 로그인 수단은 끊을 수 없다(계정 잠김 방지)
"""

from datetime import date, datetime

import httpx
import pytest

from app.core.config import get_settings
from app.core.security import hash_password
from app.models import SocialAccount, StudentProfile, User
from app.services import social_login_service as svc
from app.services.social_auth import (
    GoogleProvider,
    KakaoProvider,
    NaverProvider,
    SocialAuthError,
    SocialProfile,
)

REDIRECT = "http://localhost:5173/auth/social/callback"


def auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def social_env(monkeypatch):
    """카카오·구글은 설정됨, 네이버는 미설정(비활성 경로 검증용)."""
    st = get_settings()
    monkeypatch.setattr(st, "KAKAO_CLIENT_ID", "kakao-client")
    monkeypatch.setattr(st, "KAKAO_CLIENT_SECRET", "kakao-secret")
    monkeypatch.setattr(st, "GOOGLE_CLIENT_ID", "google-client")
    monkeypatch.setattr(st, "GOOGLE_CLIENT_SECRET", "google-secret")
    monkeypatch.setattr(st, "NAVER_CLIENT_ID", "")
    monkeypatch.setattr(st, "NAVER_CLIENT_SECRET", "")
    monkeypatch.setattr(st, "SOCIAL_REDIRECT_URIS", REDIRECT)
    return st


def _profile(**kw):
    base = dict(
        provider="kakao",
        provider_user_id="kakao-1",
        email="learner@test.dev",
        email_verified=True,
        nickname="배우미",
        birth_date=None,
    )
    base.update(kw)
    return SocialProfile(**base)


def _stub(monkeypatch, profile: SocialProfile):
    """provider 왕복을 스텁으로 — 이 프로필을 돌려주는 어댑터를 쓴다."""

    class _Adapter:
        def authorize_url(self, redirect_uri, state):
            return f"https://stub.test/authorize?redirect_uri={redirect_uri}&state={state}"

        def login(self, code, redirect_uri, state):
            assert redirect_uri == REDIRECT  # state에 담긴 값이 정본으로 전달돼야 한다
            if code == "bad-code":
                raise SocialAuthError("카카오 로그인이 거절됐어요.")
            return profile

    monkeypatch.setattr(svc, "build_provider", lambda *a, **k: _Adapter())


def _state(client, provider="kakao"):
    r = client.get(f"/api/v1/auth/social/{provider}/authorize")
    assert r.status_code == 200, r.text
    return r.json()["state"]


def _callback(client, provider="kakao", code="code-1", state=None):
    return client.post(
        f"/api/v1/auth/social/{provider}/callback", json={"code": code, "state": state}
    )


# ---------------------------------------------------------------- authorize
def test_providers_list_and_authorize_url(client, db, social_env):
    rows = {p["provider"]: p for p in client.get("/api/v1/auth/social/providers").json()["providers"]}
    assert rows["kakao"]["enabled"] is True and rows["google"]["enabled"] is True
    assert rows["naver"]["enabled"] is False  # 키가 없으면 버튼을 그리지 않는다

    body = client.get("/api/v1/auth/social/kakao/authorize").json()
    assert body["authorize_url"].startswith("https://kauth.kakao.com/oauth/authorize?")
    assert "client_id=kakao-client" in body["authorize_url"]
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A5173%2Fauth%2Fsocial%2Fcallback" in body["authorize_url"]
    assert body["state"]

    # 미설정 provider는 503, 모르는 provider는 404 — 되는 척하지 않는다
    assert client.get("/api/v1/auth/social/naver/authorize").status_code == 503
    assert client.get("/api/v1/auth/social/line/authorize").status_code == 404


def test_authorize_rejects_unlisted_redirect_uri(client, db, social_env):
    """허용목록 밖 주소는 400 — 오픈 리다이렉트로 인가 코드가 새는 것을 막는다."""
    r = client.get(
        "/api/v1/auth/social/kakao/authorize", params={"redirect_uri": "https://evil.test/steal"}
    )
    assert r.status_code == 400 and "허용되지 않은" in r.json()["detail"]
    assert client.get(
        "/api/v1/auth/social/kakao/authorize", params={"redirect_uri": REDIRECT}
    ).status_code == 200


# ---------------------------------------------------------------- 신규 가입
def test_new_user_gets_signup_token_not_account(client, db, social_env, monkeypatch):
    """★ 콜백만으로는 계정이 생기지 않는다 — 생년월일(연령 게이트) 확인 전이기 때문."""
    _stub(monkeypatch, _profile())
    body = _callback(client, state=_state(client)).json()
    assert body["status"] == "signup_required"
    assert body["signup_token"]
    assert body["profile"]["email"] == "learner@test.dev"
    assert body["profile"]["needs_birth_date"] is True  # 카카오가 생년월일을 안 줬다
    assert body["tokens"] is None
    assert db.query(StudentProfile).count() == 0
    assert db.query(SocialAccount).count() == 0

    # 생년월일을 받고 나서야 계정이 만들어진다
    r = client.post(
        "/api/v1/auth/social/signup",
        json={"signup_token": body["signup_token"], "birth_date": "2000-05-05"},
    )
    assert r.status_code == 200, r.text
    signed = r.json()
    assert signed["status"] == "logged_in" and signed["is_new_account"] is True
    assert signed["tokens"]["access_token"]
    student = db.query(StudentProfile).one()
    assert student.student_login_id == "learner@test.dev"  # 이메일이 곧 로그인 아이디
    assert student.birth_date == date(2000, 5, 5)
    assert student.organization_id is None  # 소셜 가입은 항상 무소속

    # 발급된 토큰이 실제로 학생 권한으로 통한다
    me = client.get("/api/v1/auth/me", headers=auth(signed["tokens"]["access_token"]))
    assert me.status_code == 200 and me.json()["role"] == "student"

    # 두 번째 로그인은 곧바로 통과(가입 화면 없음)
    again = _callback(client, state=_state(client)).json()
    assert again["status"] == "logged_in" and again["student"]["id"] == student.id
    assert db.query(StudentProfile).count() == 1


def test_social_signup_blocks_under_14(client, db, social_env, monkeypatch):
    """만 14세 미만은 소셜로 가입시키지 않는다 — 보호자 동의 절차가 이 흐름에 없다."""
    _stub(monkeypatch, _profile())
    token = _callback(client, state=_state(client)).json()["signup_token"]
    minor = date.today().replace(year=date.today().year - 10).isoformat()
    r = client.post(
        "/api/v1/auth/social/signup", json={"signup_token": token, "birth_date": minor}
    )
    assert r.status_code == 400 and "보호자" in r.json()["detail"]
    assert db.query(StudentProfile).count() == 0  # 계정이 남지 않는다


def test_provider_birth_date_is_used_and_cannot_be_overridden(client, db, social_env, monkeypatch):
    """provider가 생년월일을 주면 그 값이 정본 — 사용자가 성인 생일을 보내도 못 덮는다."""
    _stub(monkeypatch, _profile(birth_date=date.today().replace(year=date.today().year - 9)))
    body = _callback(client, state=_state(client)).json()
    assert body["profile"]["needs_birth_date"] is False
    r = client.post(
        "/api/v1/auth/social/signup",
        json={"signup_token": body["signup_token"], "birth_date": "1990-01-01"},
    )
    assert r.status_code == 400 and "보호자" in r.json()["detail"]


def test_signup_without_email_uses_synthetic_login_id(client, db, social_env, monkeypatch):
    """카카오는 이메일 제공이 선택 — 이메일이 없어도 가입이 성립해야 한다."""
    _stub(monkeypatch, _profile(email=None, email_verified=False, provider_user_id="kakao-77"))
    body = _callback(client, state=_state(client)).json()
    r = client.post(
        "/api/v1/auth/social/signup",
        json={"signup_token": body["signup_token"], "birth_date": "1999-01-02", "nickname": "무이메일"},
    )
    assert r.status_code == 200, r.text
    student = db.query(StudentProfile).one()
    assert student.student_login_id == "kakao_kakao-77" and student.nickname == "무이메일"


# ---------------------------------------------------------------- 기존 계정 연결
def _make_student(db, email="learner@test.dev", password="Password123!"):
    st = StudentProfile(
        student_login_id=email,
        student_code="CAT-EXIST",
        password_hash=hash_password(password),
        nickname="기존학생",
        birth_date=date(1998, 3, 3),
    )
    db.add(st)
    db.commit()
    return st


def test_verified_email_links_to_existing_account(client, db, social_env, monkeypatch):
    existing = _make_student(db)
    _stub(monkeypatch, _profile(email_verified=True))
    body = _callback(client, state=_state(client)).json()
    assert body["status"] == "logged_in" and body["linked_now"] is True
    assert body["student"]["id"] == existing.id
    assert db.query(StudentProfile).count() == 1  # 중복 계정을 만들지 않는다
    link = db.query(SocialAccount).one()
    assert link.student_id == existing.id and link.provider == "kakao"


def test_unverified_email_never_auto_links(client, db, social_env, monkeypatch):
    """★ provider가 이메일 소유를 확인해 주지 않으면 남의 계정에 붙을 수 있다 → 409."""
    _make_student(db)
    _stub(monkeypatch, _profile(email_verified=False))
    r = _callback(client, state=_state(client))
    assert r.status_code == 409 and "로그인한 뒤" in r.json()["detail"]
    assert db.query(SocialAccount).count() == 0


def test_console_account_email_is_rejected(client, db, social_env, monkeypatch):
    """운영자·강사 계정은 소셜 로그인 대상이 아니다(고권한 계정 공격면 축소)."""
    db.add(
        User(
            email="staff@test.dev",
            password_hash=hash_password("Password123!"),
            name="강사",
            role="instructor",
            email_verified_at=datetime.now(),
        )
    )
    db.commit()
    _stub(monkeypatch, _profile(email="staff@test.dev"))
    r = _callback(client, state=_state(client))
    assert r.status_code == 400 and "콘솔 계정" in r.json()["detail"]


def test_disabled_account_cannot_log_in(client, db, social_env, monkeypatch):
    existing = _make_student(db)
    db.add(SocialAccount(student_id=existing.id, provider="kakao", provider_user_id="kakao-1"))
    existing.status = "disabled"
    db.commit()
    _stub(monkeypatch, _profile())
    assert _callback(client, state=_state(client)).status_code == 403


# ---------------------------------------------------------------- state 검증
def test_state_must_be_signed_and_match_provider(client, db, social_env, monkeypatch):
    _stub(monkeypatch, _profile())
    assert _callback(client, state="not-a-token").status_code == 400
    # 구글용으로 발급한 state를 카카오 콜백에 쓰면 거절 (교차 사용 차단)
    google_state = _state(client, "google")
    assert _callback(client, provider="kakao", state=google_state).status_code == 400
    # 서명이 유효해도 provider 실패는 400으로 정직하게 전달
    assert _callback(client, code="bad-code", state=_state(client)).status_code == 400


def test_expired_state_is_rejected(client, db, social_env, monkeypatch):
    monkeypatch.setattr(svc, "STATE_TTL_SECONDS", -1)  # 이미 만료된 state 발급
    _stub(monkeypatch, _profile())
    r = _callback(client, state=_state(client))
    assert r.status_code == 400 and "만료" in r.json()["detail"]


# ---------------------------------------------------------------- 연결 관리
def _social_login(client, db, monkeypatch, profile=None):
    _stub(monkeypatch, profile or _profile())
    body = _callback(client, state=_state(client)).json()
    if body["status"] == "signup_required":
        body = client.post(
            "/api/v1/auth/social/signup",
            json={"signup_token": body["signup_token"], "birth_date": "1997-07-07"},
        ).json()
    return body["tokens"]["access_token"]


def test_connections_and_disconnect_guard(client, db, social_env, monkeypatch):
    token = _social_login(client, db, monkeypatch)
    body = client.get("/api/v1/auth/social/connections", headers=auth(token)).json()
    assert [c["provider"] for c in body["connections"]] == ["kakao"]
    assert body["has_password"] is False  # 소셜 전용 계정

    # ★ 마지막 로그인 수단은 끊을 수 없다 — 끊으면 계정에 다시 못 들어온다
    r = client.delete("/api/v1/auth/social/kakao", headers=auth(token))
    assert r.status_code == 400 and "마지막 로그인 수단" in r.json()["detail"]
    assert db.query(SocialAccount).count() == 1

    # 구글을 추가로 연결하면 카카오는 해제할 수 있다
    _stub(monkeypatch, _profile(provider="google", provider_user_id="google-1"))
    connected = client.post(
        "/api/v1/auth/social/google/connect",
        json={"code": "code-1", "state": _state(client, "google")},
        headers=auth(token),
    )
    assert connected.status_code == 200
    assert {c["provider"] for c in connected.json()["connections"]} == {"kakao", "google"}
    left = client.delete("/api/v1/auth/social/kakao", headers=auth(token))
    assert left.status_code == 200
    assert [c["provider"] for c in left.json()["connections"]] == ["google"]


def test_cannot_steal_social_account_linked_elsewhere(client, db, social_env, monkeypatch):
    """다른 학생에 이미 연결된 소셜 계정은 내 계정에 붙일 수 없다."""
    other = _make_student(db, email="other@test.dev")
    db.add(SocialAccount(student_id=other.id, provider="google", provider_user_id="google-9"))
    db.commit()
    token = _social_login(client, db, monkeypatch)
    _stub(monkeypatch, _profile(provider="google", provider_user_id="google-9"))
    r = client.post(
        "/api/v1/auth/social/google/connect",
        json={"code": "code-1", "state": _state(client, "google")},
        headers=auth(token),
    )
    assert r.status_code == 409


def test_social_only_account_cannot_log_in_with_password(client, db, social_env, monkeypatch):
    """소셜 전용 계정의 password_hash 자리값으로는 어떤 비밀번호도 통하지 않는다."""
    _social_login(client, db, monkeypatch)
    for guess in ("", svc.UNUSABLE_PASSWORD, "Password123!"):
        r = client.post(
            "/api/v1/auth/student-login",
            json={"student_login_id": "learner@test.dev", "password": guess},
        )
        assert r.status_code == 401


def test_connections_require_student_token(client, db, social_env):
    assert client.get("/api/v1/auth/social/connections").status_code == 401
    assert client.delete("/api/v1/auth/social/kakao").status_code == 401


# ---------------------------------------------------------------- provider 어댑터 단위
def test_kakao_adapter_parses_profile_and_birth():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            assert b"grant_type=authorization_code" in request.content
            assert b"client_secret=sec" in request.content
            return httpx.Response(200, json={"access_token": "kakao-at"})
        assert request.headers["Authorization"] == "Bearer kakao-at"
        return httpx.Response(
            200,
            json={
                "id": 4823,
                "kakao_account": {
                    "email": "Kid@Test.dev",
                    "is_email_valid": True,
                    "is_email_verified": True,
                    "birthyear": "2001",
                    "birthday": "0203",
                    "profile": {"nickname": "하은"},
                },
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        p = KakaoProvider("cid", "sec", client=http).login("code", REDIRECT, "state")
    assert p.provider_user_id == "4823" and p.email == "kid@test.dev"  # 소문자 정규화
    assert p.email_verified is True and p.nickname == "하은"
    assert p.birth_date == date(2001, 2, 3)


def test_kakao_email_not_verified_when_only_valid():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "at"})
        return httpx.Response(
            200,
            json={"id": 1, "kakao_account": {"email": "a@b.dev", "is_email_valid": True}},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        p = KakaoProvider("cid", client=http).login("code", REDIRECT, "state")
    assert p.email == "a@b.dev" and p.email_verified is False
    assert p.birth_date is None  # 생년월일 미동의 → 추측하지 않는다


def test_naver_adapter_rejects_non_success_resultcode():
    def handler(request: httpx.Request) -> httpx.Response:
        if "token" in request.url.path:
            return httpx.Response(200, json={"access_token": "naver-at"})
        # 네이버는 실패도 HTTP 200으로 준다 — 200만으로 성공을 판단하면 안 된다
        return httpx.Response(200, json={"resultcode": "024", "message": "Authentication failed"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(SocialAuthError, match="네이버"):
            NaverProvider("cid", "sec", client=http).login("code", REDIRECT, "state")


def test_naver_adapter_parses_profile():
    def handler(request: httpx.Request) -> httpx.Response:
        if "token" in request.url.path:
            assert b"state=st-1" in request.content  # 네이버는 토큰 교환에도 state를 요구
            return httpx.Response(200, json={"access_token": "naver-at"})
        return httpx.Response(
            200,
            json={
                "resultcode": "00",
                "response": {
                    "id": "nv-1",
                    "email": "n@test.dev",
                    "nickname": "네이버유저",
                    "birthyear": "1998",
                    "birthday": "03-02",
                },
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        p = NaverProvider("cid", "sec", client=http).login("code", REDIRECT, "st-1")
    assert p.provider_user_id == "nv-1" and p.birth_date == date(1998, 3, 2)
    assert p.email_verified is True  # 네이버는 가입 시 이메일 본인확인을 거친다


def test_google_adapter_uses_email_verified_flag():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "g-at"})
        return httpx.Response(
            200,
            json={"sub": "g-1", "email": "g@test.dev", "email_verified": False, "name": "구글유저"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        p = GoogleProvider("cid", "sec", client=http).login("code", REDIRECT, "state")
    assert p.provider_user_id == "g-1" and p.email_verified is False
    assert p.birth_date is None  # 구글 userinfo에는 생년월일이 없다


def test_provider_without_client_id_fails_loudly():
    with pytest.raises(SocialAuthError, match="설정"):
        KakaoProvider("", "")


def test_anonymize_student_purges_social_links(client, db, social_env, monkeypatch):
    """탈퇴(익명화)하면 연결 행의 provider 이메일(식별 PII)까지 파기된다."""
    from app.services.privacy_service import anonymize_student

    _social_login(client, db, monkeypatch)
    student = db.query(StudentProfile).one()
    assert db.query(SocialAccount).count() == 1

    assert anonymize_student(db, student) is True
    db.commit()
    assert db.query(SocialAccount).count() == 0
