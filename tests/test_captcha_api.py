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


def _org_admin(client, db, org):
    admin = User(
        email="admin@t.dev", password_hash=hash_password("Password123!"), name="교장",
        role="org_admin", organization_id=org.id, email_verified_at=datetime.utcnow(),
    )
    db.add(admin)
    db.commit()
    r = client.post("/api/v1/auth/login",
                    json={"role": "teacher", "email": "admin@t.dev", "password": "Password123!"})
    return r.json()["access_token"]


def test_subject_scope_enforced_for_external_key(client, db, seed_org):
    """외부 판매 키(first_party=False)는 발급 과목에 고정 — ?subject=로 다른 과목 못 받는다.
    1st-party 키만 과목 전환 허용."""
    org = seed_org["org"]
    _, pro = _plans(db)
    db.add(Subscription(organization_id=org.id, plan_id=pro.id, status="active"))
    db.commit()
    tok = _ops(client, db)

    ext = client.post("/api/v1/ops/api-keys",
                      json={"organization_id": org.id, "product": "edu", "subject": "국어"},
                      headers=auth(tok)).json()["site_key"]
    ch = client.post("/api/v1/captcha/v1/challenge", params={"subject": "수학"},
                     headers={"X-Site-Key": ext})
    assert ch.status_code == 200
    assert ch.json()["subject"] == "국어"  # 수학 요청 무시, 발급 과목 고정

    fp = client.post("/api/v1/ops/api-keys",
                     json={"organization_id": org.id, "product": "edu", "subject": "국어",
                           "first_party": True},
                     headers=auth(tok)).json()["site_key"]
    ch2 = client.post("/api/v1/captcha/v1/challenge", params={"subject": "수학"},
                      headers={"X-Site-Key": fp})
    assert ch2.json()["subject"] == "수학"  # 1st-party는 전환 허용


def test_ops_reset_operator_password(client, db, seed_org):
    """운영자 임시 비번 재설정 — 새 임시비번 반환·비번 해시 변경·must_change_password=True. 비운영자 대상은 404."""
    from app.models import User
    from app.core.security import verify_password

    tok = _ops(client, db)
    created = client.post("/api/v1/ops/operators",
                          json={"name": "리셋대상", "email": "reset-op@t.dev"}, headers=auth(tok)).json()
    op_id = created["id"]
    old_temp = created["temp_password"]

    r = client.post(f"/api/v1/ops/operators/{op_id}/reset-password", headers=auth(tok))
    assert r.status_code == 200, r.text
    new_temp = r.json()["temp_password"]
    assert new_temp and new_temp != old_temp
    assert r.json()["email"] == "reset-op@t.dev"

    db.expire_all()
    op = db.get(User, op_id)
    assert op.must_change_password is True
    assert verify_password(new_temp, op.password_hash)       # 새 임시비번으로 인증됨
    assert not verify_password(old_temp, op.password_hash)   # 옛 비번은 무효

    # 운영자가 아닌 대상 → 404
    bad = client.post("/api/v1/ops/operators/00000000-0000-0000-0000-000000000000/reset-password",
                      headers=auth(tok))
    assert bad.status_code == 404


def test_assert_entitled_bypasses_first_party(db, seed_org):
    """1st-party(인앱 dogfooding) 키는 기관 요금제와 무관하게 통과한다.
    학교가 Basic/무플랜이어도 학생 인앱 학습이 '교육형 API를 쓸 수 없어요'(402)로 막히면 안 된다.
    (assert_entitled는 ApiKey를 DB조회하지 않고 org_id/product/first_party만 보므로 메모리 객체로 검증)"""
    import pytest
    from fastapi import HTTPException
    from app.models import ApiKey
    from app.services import captcha_service as cs

    org = seed_org["org"]  # 구독/플랜 없음 → allowed_products = ['captcha']만
    ext = ApiKey(organization_id=org.id, product="edu", first_party=False)
    fp = ApiKey(organization_id=org.id, product="edu", first_party=True)

    # 외부(비-first_party) edu 키: 요금제에 edu 없어 402
    with pytest.raises(HTTPException) as e:
        cs.assert_entitled(db, ext)
    assert e.value.status_code == 402

    # 1st-party 키: 같은 무플랜 org라도 게이트 우회(예외 없이 통과)
    cs.assert_entitled(db, fp)


def test_org_admin_self_service_scoped_to_purchase(client, db, seed_org):
    """기관 관리자(교장)는 구매 과목 내에서만 키 발급 — 교사는 접근 불가."""
    org = seed_org["org"]
    _, pro = _plans(db)
    db.add(Subscription(organization_id=org.id, plan_id=pro.id, status="active"))
    org.edu_subjects = ["국어"]  # 국어만 구매
    db.commit()
    admin = _org_admin(client, db, org)

    ent = client.get(f"/api/v1/orgs/{org.id}/api-entitlements", headers=auth(admin)).json()
    assert "edu" in ent["products"] and ent["edu_subjects"] == ["국어"]

    ok = client.post(f"/api/v1/orgs/{org.id}/api-keys",
                     json={"product": "edu", "subject": "국어", "label": "우리학교"},
                     headers=auth(admin))
    assert ok.status_code == 200, ok.text
    assert ok.json()["secret_key"] and ok.json()["first_party"] is False
    assert ok.json()["subject"] == "국어"

    denied = client.post(f"/api/v1/orgs/{org.id}/api-keys",
                         json={"product": "edu", "subject": "수학"}, headers=auth(admin))
    assert denied.status_code == 402  # 구매 안 한 과목

    keys = client.get(f"/api/v1/orgs/{org.id}/api-keys", headers=auth(admin)).json()
    assert len(keys) >= 1
    assert client.delete(f"/api/v1/orgs/{org.id}/api-keys/{keys[0]['id']}",
                         headers=auth(admin)).status_code == 200

    # 교사 토큰(비교장) → 403
    tt = client.post("/api/v1/auth/login",
                     json={"role": "teacher", "email": "t1@test.dev", "password": "Password123!"}
                     ).json()["access_token"]
    assert client.get(f"/api/v1/orgs/{org.id}/api-keys", headers=auth(tt)).status_code == 403


def test_verify_rejects_cross_subject_token(client, db, seed_org):
    """외부 판매 키는 발급 과목 외 챌린지 토큰을 verify할 수 없다.

    1st-party(국어) 키가 낸 국어 토큰을, 외부 판매 수학 키가 verify해서 구매 안 한
    국어 과목의 채점·행동데이터 수집을 하는 것을 막는다(토큰 유출 대비 심층 방어).
    """
    org = seed_org["org"]
    _, pro = _plans(db)
    db.add(Subscription(organization_id=org.id, plan_id=pro.id, status="active"))
    db.commit()
    tok = _ops(client, db)

    # 1st-party 국어 키로 국어 챌린지 발급 → 국어 토큰 확보
    fp = client.post("/api/v1/ops/api-keys",
                     json={"organization_id": org.id, "product": "edu", "subject": "국어",
                           "first_party": True},
                     headers=auth(tok)).json()["site_key"]
    ch = client.post("/api/v1/captcha/v1/challenge",
                     headers={"X-Site-Key": fp}).json()
    assert ch["subject"] == "국어"
    kor_token = ch["challenge_token"]

    # 외부 판매 수학 키
    ext_math = client.post("/api/v1/ops/api-keys",
                           json={"organization_id": org.id, "product": "edu", "subject": "수학"},
                           headers=auth(tok)).json()["site_key"]

    # 외부 수학 키로 국어 토큰 verify 시도 → 403 (구매 과목 밖)
    r = client.post("/api/v1/captcha/v1/verify",
                    json={"challenge_token": kor_token, "answer": "x"},
                    headers={"X-Site-Key": ext_math})
    assert r.status_code == 403, r.text

    # 같은 과목(수학) 외부 키는 자기 수학 토큰을 정상 verify
    ch2 = client.post("/api/v1/captcha/v1/challenge",
                      headers={"X-Site-Key": ext_math}).json()
    assert ch2["subject"] == "수학"
    r2 = client.post("/api/v1/captcha/v1/verify",
                     json={"challenge_token": ch2["challenge_token"], "answer": "x"},
                     headers={"X-Site-Key": ext_math})
    assert r2.status_code == 200, r2.text
