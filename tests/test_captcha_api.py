"""메인·교육형 캡차 API — 키 발급·요금제 게이팅·챌린지/검증."""

from datetime import datetime

from app.core.security import hash_password
from app.models import Organization, Plan, Subscription, User


def auth(t):
    return {"Authorization": f"Bearer {t}"}


def _ops(client, db):
    ops = User(
        email="ops@t.dev", password_hash=hash_password("Password123!"), name="운영자",
        role="ops", email_verified_at=datetime.utcnow(),
    )
    db.add(ops)
    db.commit()
    r = client.post("/api/v1/auth/ops-login", json={"email": "ops@t.dev", "password": "Password123!"})
    return r.json()["access_token"]


def _plans(db):
    basic = Plan(key="Basic", name="Basic", monthly_price=99000, api_quota=100)
    pro = Plan(key="Pro", name="Pro", monthly_price=290000, api_quota=1000)
    db.add_all([basic, pro])
    db.commit()
    return basic, pro


def test_issue_and_challenge_verify(client, db, seed_org):
    org = seed_org["org"]
    basic, pro = _plans(db)
    db.add(Subscription(organization_id=org.id, plan_id=pro.id, status="active"))
    db.commit()
    tok = _ops(client, db)

    # 메인 캡차 키 발급 (Pro는 captcha 허용)
    r = client.post(
        "/api/v1/ops/api-keys",
        json={"organization_id": org.id, "product": "captcha", "label": "우리 사이트", "domain": "example.kr"},
        headers=auth(tok),
    )
    assert r.status_code == 200, r.text
    site_key = r.json()["site_key"]
    secret_key = r.json()["secret_key"]
    assert site_key.startswith("ck_captcha_")

    # 도메인 지정 키 — Origin 불일치/부재는 403, 등록 도메인(서브도메인 포함)은 통과
    bad = client.post(
        "/api/v1/captcha/v1/challenge",
        headers={"X-Site-Key": site_key, "Origin": "https://evil.com"},
    )
    assert bad.status_code == 403
    noorigin = client.post("/api/v1/captcha/v1/challenge", headers={"X-Site-Key": site_key})
    assert noorigin.status_code == 403

    # 챌린지 발급 (정답 미포함)
    ok_origin = {"X-Site-Key": site_key, "Origin": "https://www.example.kr"}
    ch = client.post("/api/v1/captcha/v1/challenge", headers=ok_origin)
    assert ch.status_code == 200, ch.text
    body = ch.json()
    assert "challenge_token" in body and "answer" not in body

    # 정답 맞히기 → verdict, 서버 재검증(1회용)
    if body["type"] == "arithmetic":
        # a+b = 프롬프트에서 계산
        import re

        a, b = map(int, re.findall(r"\d+", body["prompt"])[:2])
        answer = str(a + b)
    else:  # image_select — 정답을 모르므로 verify가 틀려도 흐름만 확인
        answer = [c["id"] for c in body["cells"][:1]]
    vr = client.post(
        "/api/v1/captcha/v1/verify",
        json={"challenge_token": body["challenge_token"], "answer": answer},
        headers=ok_origin,
    )
    assert vr.status_code == 200
    if vr.json()["success"]:
        vt = vr.json()["verdict_token"]
        val = client.post("/api/v1/captcha/v1/validate", json={"verdict_token": vt}, headers={"X-Secret-Key": secret_key})
        assert val.json()["success"] is True
        # 1회용 — 재검증은 실패
        val2 = client.post("/api/v1/captcha/v1/validate", json={"verdict_token": vt}, headers={"X-Secret-Key": secret_key})
        assert val2.json()["success"] is False


def test_edu_key_requires_subject_and_plan(client, db, seed_org):
    org = seed_org["org"]
    basic, pro = _plans(db)
    tok = _ops(client, db)

    # 구독 없음(=미구독) → 교육형 발급 거부(402)
    r0 = client.post(
        "/api/v1/ops/api-keys",
        json={"organization_id": org.id, "product": "edu", "subject": "생활"},
        headers=auth(tok),
    )
    assert r0.status_code == 402

    # Basic 구독 → 교육형 여전히 불가(Basic은 captcha만)
    db.add(Subscription(organization_id=org.id, plan_id=basic.id, status="active"))
    db.commit()
    r1 = client.post(
        "/api/v1/ops/api-keys",
        json={"organization_id": org.id, "product": "edu", "subject": "생활"},
        headers=auth(tok),
    )
    assert r1.status_code == 402

    # Pro로 교체 → 교육형 발급 OK, 그 과목 챌린지
    db.query(Subscription).filter(Subscription.organization_id == org.id).delete()
    db.add(Subscription(organization_id=org.id, plan_id=pro.id, status="active"))
    db.commit()
    r2 = client.post(
        "/api/v1/ops/api-keys",
        json={"organization_id": org.id, "product": "edu", "subject": "생활"},
        headers=auth(tok),
    )
    assert r2.status_code == 200, r2.text
    ch = client.post("/api/v1/captcha/v1/challenge", headers={"X-Site-Key": r2.json()["site_key"]})
    assert ch.status_code == 200
    assert ch.json()["subject"] == "생활"
    # 교육형은 실문항(객관식·조작형·따라쓰기·길찾기·퍼즐) 외에 동작형(드래그·따라그리기)도 출제된다
    assert ch.json()["type"] in {
        "single", "multi", "connect", "sort", "order", "place", "route", "puzzle",
        "drag_drop", "trace_path",
    }

    # subject 없이 edu 발급 → 400
    rbad = client.post(
        "/api/v1/ops/api-keys",
        json={"organization_id": org.id, "product": "edu"},
        headers=auth(tok),
    )
    assert rbad.status_code == 400


def _issue_captcha_key(client, db, seed_org):
    """Pro 구독 + 도메인 미지정 captcha 키 발급 헬퍼."""
    org = seed_org["org"]
    basic, pro = _plans(db)
    db.add(Subscription(organization_id=org.id, plan_id=pro.id, status="active"))
    db.commit()
    tok = _ops(client, db)
    r = client.post(
        "/api/v1/ops/api-keys",
        json={"organization_id": org.id, "product": "captcha", "label": "테스트"},
        headers=auth(tok),
    )
    assert r.status_code == 200, r.text
    return r.json()["site_key"]


def test_domainless_key_allows_any_origin(client, db, seed_org):
    """도메인 미지정 키(개발·테스트용)는 어느 출처든, Origin 없이도 동작."""
    site_key = _issue_captcha_key(client, db, seed_org)
    r1 = client.post(
        "/api/v1/captcha/v1/challenge",
        headers={"X-Site-Key": site_key, "Origin": "https://anything.example"},
    )
    assert r1.status_code == 200
    r2 = client.post("/api/v1/captcha/v1/challenge", headers={"X-Site-Key": site_key})
    assert r2.status_code == 200


def test_challenge_rate_limited(client, db, seed_org, monkeypatch):
    """공개 챌린지 엔드포인트 IP 레이트리밋 — 한도 초과 시 429."""
    from app.api.v1.endpoints import captcha_api as capi

    monkeypatch.setattr(capi, "RATE_CHALLENGE_PER_MIN", 3)
    site_key = _issue_captcha_key(client, db, seed_org)
    for _ in range(3):
        assert (
            client.post("/api/v1/captcha/v1/challenge", headers={"X-Site-Key": site_key}).status_code
            == 200
        )
    over = client.post("/api/v1/captcha/v1/challenge", headers={"X-Site-Key": site_key})
    assert over.status_code == 429


def test_captcha_cors_preflight_any_origin(client):
    """외부 고객사 도메인의 preflight가 전역 CORS 허용목록에 막히지 않아야 한다."""
    r = client.options(
        "/api/v1/captcha/v1/challenge",
        headers={"Origin": "https://customer.example", "Access-Control-Request-Method": "POST"},
    )
    assert r.status_code == 204
    assert r.headers["access-control-allow-origin"] == "*"
    assert "X-Site-Key" in r.headers["access-control-allow-headers"]
