"""적대적 검토 수정사항 회귀 테스트.

- A1: 오답+완료신고로는 오늘의퀴즈 done 승격 불가 (랭킹/상장 위조 차단)
- A9: 승인(active) 전 기관 소속 계정 로그인 차단
- B7: 위젯 데모 XSS 이스케이프 + api 화이트리스트 + CSP
- B11: 캡차 챌린지 토큰에서 정답 복원 불가(암호화) + 챌린지 1회용
"""

import re
from datetime import datetime

from app.core.security import hash_password
from app.models import DailyQuizStatus, Membership, Organization, Plan, Subscription, User


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def _student_token(client, seed_org):
    return client.post(
        "/api/v1/auth/student-login",
        json={"organization_id": seed_org["org"].id, "student_login_id": "stu01", "password": "1234"},
    ).json()["access_token"]


# ---------------------------------------------------------------- A1
def test_incorrect_completed_does_not_mark_done(client, db, seed_org):
    """오답인데 completed=true 로 신고해도 done 으로 승격되지 않는다 (랭킹 만점 위조 차단)."""
    from datetime import date

    token = _student_token(client, seed_org)
    r = client.post(
        "/api/v1/learning/attempts",
        json={"subject": "국어", "result": "incorrect", "completed": True},
        headers=auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["coins_earned"] == 0  # 오답 보상 없음
    quiz = (
        db.query(DailyQuizStatus)
        .filter(
            DailyQuizStatus.student_id == seed_org["student"].id,
            DailyQuizStatus.quiz_date == date.today(),
            DailyQuizStatus.subject == "국어",
        )
        .first()
    )
    assert quiz is not None and quiz.status != "done"  # 진행중일 뿐 완료 아님

    # 정답+완료면 정상적으로 done
    r2 = client.post(
        "/api/v1/learning/attempts",
        json={"subject": "국어", "result": "correct", "completed": True},
        headers=auth(token),
    )
    assert r2.status_code == 200
    db.expire_all()
    quiz2 = (
        db.query(DailyQuizStatus)
        .filter(
            DailyQuizStatus.student_id == seed_org["student"].id,
            DailyQuizStatus.quiz_date == date.today(),
            DailyQuizStatus.subject == "국어",
        )
        .first()
    )
    assert quiz2.status == "done"


# ---------------------------------------------------------------- A9
def test_pending_org_blocks_login_until_approved(client, db):
    """org.status/membership 이 승인(active) 되기 전에는 org_admin 로그인 불가(403)."""
    org = Organization(name="대기기관", code="PN-EDU-0001", org_type="유치원", status="pending")
    db.add(org)
    db.flush()
    u = User(
        email="pending-admin@test.dev",
        password_hash=hash_password("Password123!"),
        name="교장",
        role="org_admin",
        organization_id=org.id,
        email_verified_at=datetime.utcnow(),
    )
    db.add(u)
    db.flush()
    db.add(Membership(user_id=u.id, organization_id=org.id, role="org_admin", status="pending"))
    db.commit()

    r = client.post(
        "/api/v1/auth/login",
        json={"email": "pending-admin@test.dev", "password": "Password123!"},
    )
    assert r.status_code == 403
    assert "승인" in str(r.json())

    # ops 승인 흐름(active 전환) 후에는 로그인 성공
    org.status = "active"
    m = db.query(Membership).filter(Membership.user_id == u.id).first()
    m.status = "active"
    db.commit()
    r2 = client.post(
        "/api/v1/auth/login",
        json={"email": "pending-admin@test.dev", "password": "Password123!"},
    )
    assert r2.status_code == 200


# ---------------------------------------------------------------- B7
def test_widget_demo_escapes_input_and_sets_csp(client):
    """site_key XSS 이스케이프 + api 화이트리스트 불일치 시 기본값 + CSP 헤더."""
    r = client.get(
        "/api/v1/widget/demo",
        params={"site_key": '"><script>alert(1)</script>', "api": "javascript:alert(1)"},
    )
    assert r.status_code == 200
    # 주입한 스크립트는 이스케이프되어 실행 태그로 남지 않는다
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text
    # 화이트리스트에 안 맞는 api 는 기본값으로 대체
    assert "javascript:alert(1)" not in r.text
    assert "/api/v1/widget/catchap-widget.js" in r.text
    assert "Content-Security-Policy" in r.headers


# ---------------------------------------------------------------- B11 (unit)
def test_challenge_token_answer_not_recoverable():
    """챌린지 토큰은 서버 키로만 복호화 — 클라이언트가 base64로 정답을 뽑을 수 없다."""
    import base64
    import json

    from app.services import captcha_service as cs

    ch = cs.make_challenge("captcha", None)
    tok = ch["challenge_token"]
    assert "answer" not in ch  # 공개 응답에 정답 없음
    # 서버(키 보유)는 복호화 가능
    data = cs._unsign(tok)
    assert data is not None and "a" in data
    # 과거 결함(base64 평문) 재현 시도 → 정답 페이로드를 얻을 수 없어야 한다
    recovered = None
    for candidate in (tok, tok.split(".")[0]):
        try:
            recovered = json.loads(base64.urlsafe_b64decode(candidate + "==="))
            break
        except Exception:
            recovered = None
    assert recovered is None or "a" not in recovered


# ---------------------------------------------------------------- B11 (flow)
def _ops_token(client, db):
    ops = User(
        email="ops-h@t.dev", password_hash=hash_password("Password123!"), name="운영자",
        role="ops", email_verified_at=datetime.utcnow(),
    )
    db.add(ops)
    db.commit()
    return client.post(
        "/api/v1/auth/ops-login", json={"email": "ops-h@t.dev", "password": "Password123!"}
    ).json()["access_token"]


def test_challenge_is_single_use(client, db, seed_org):
    """검증에 통과한 challenge_token 은 1회용 — 같은 토큰 재검증은 409(리플레이 차단)."""
    org = seed_org["org"]
    pro = Plan(key="Pro", name="Pro", monthly_price=1, api_quota=100000)
    db.add(pro)
    db.commit()
    db.add(Subscription(organization_id=org.id, plan_id=pro.id, status="active"))
    db.commit()
    tok = _ops_token(client, db)
    r = client.post(
        "/api/v1/ops/api-keys",
        json={"organization_id": org.id, "product": "captcha", "domain": "ex.kr"},
        headers=auth(tok),
    )
    site_key = r.json()["site_key"]
    # 도메인 지정 키 → 등록 도메인 Origin에서만 호출 가능
    hdrs = {"X-Site-Key": site_key, "Origin": "https://ex.kr"}

    # arithmetic 챌린지가 나올 때까지 재발급(정답을 프롬프트로 계산할 수 있는 유형)
    body = None
    for _ in range(30):
        body = client.post("/api/v1/captcha/v1/challenge", headers=hdrs).json()
        if body["type"] == "arithmetic":
            break
    assert body["type"] == "arithmetic"
    a, b = map(int, re.findall(r"\d+", body["prompt"])[:2])
    payload = {"challenge_token": body["challenge_token"], "answer": str(a + b)}

    v1 = client.post("/api/v1/captcha/v1/verify", json=payload, headers=hdrs)
    assert v1.status_code == 200 and v1.json()["success"] is True
    # 동일 challenge_token 재사용 → 이미 사용됨(409)
    v2 = client.post("/api/v1/captcha/v1/verify", json=payload, headers=hdrs)
    assert v2.status_code == 409

    # 오답도 챌린지를 소비한다 — 같은 토큰으로 정답 나올 때까지 재시도(브루트포스) 차단
    body = None
    for _ in range(30):
        body = client.post("/api/v1/captcha/v1/challenge", headers=hdrs).json()
        if body["type"] == "arithmetic":
            break
    assert body["type"] == "arithmetic"
    a, b = map(int, re.findall(r"\d+", body["prompt"])[:2])
    wrong = client.post(
        "/api/v1/captcha/v1/verify",
        json={"challenge_token": body["challenge_token"], "answer": str(a + b + 1)},
        headers=hdrs,
    )
    assert wrong.status_code == 200 and wrong.json()["success"] is False
    retry_correct = client.post(
        "/api/v1/captcha/v1/verify",
        json={"challenge_token": body["challenge_token"], "answer": str(a + b)},
        headers=hdrs,
    )
    assert retry_correct.status_code == 409
