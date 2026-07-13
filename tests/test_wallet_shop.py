"""코인 경제 재현 테스트 — 위젯 적립 경로(코인·학습시간)와 상점 구매(차감·보유 반영).

'코인 획득이 안 된다 / 상점 차감·보유가 안 된다' 제보의 백엔드 경로 검증.
"""

from app.services import captcha_service as cs
from app.services import design_data as D
from app.services.korean_bank import KOREAN_FULL


def _student_token(client):
    r = client.post(
        "/api/v1/auth/student-login",
        json={"student_login_id": "stu01", "password": "1234"},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _first_party_key(db):
    from app.models import ApiKey, Organization, Site

    platform = Organization(name="CatChap플랫폼", code="TS-CAT-9100", org_type="플랫폼")
    db.add(platform)
    db.flush()
    site = Site(organization_id=platform.id, name="inapp", domain="")
    db.add(site)
    db.flush()
    key = ApiKey(
        organization_id=platform.id, site_id=site.id, product="edu", subject="국어",
        site_key="ck_edu_testfp", secret_key_hash="x", first_party=True,
    )
    db.add(key)
    db.commit()
    return key


def _dictation_challenge(subject="국어"):
    q = next(q for q in KOREAN_FULL if q["type"] == "dictation")
    ch = cs._wrap_bank_question(subject, q, {"subj": subject, "qid": q["id"]})
    return ch, q["answer"]


def test_widget_verify_awards_coins_and_solve_time(client, db, seed_org):
    """인앱 위젯 경로: 정답 1건 → 코인 +10 적립 + 풀이시간 실측 저장."""
    from app.models import CoinTransaction, LearningAttempt

    _first_party_key(db)
    tok = _student_token(client)
    ch, answer = _dictation_challenge()

    r = client.post(
        "/api/v1/captcha/v1/verify",
        json={
            "challenge_token": ch["challenge_token"],
            "answer": answer,
            "behavior": {"solve_time_ms": 5321, "input_type": "touch"},
        },
        headers={"X-Site-Key": "ck_edu_testfp", "Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    # 적립 결과가 응답에 실려 프론트 코인 표시가 갱신된다
    sess = body.get("session")
    assert sess is not None, "인증 학생인데 적립(session)이 빠졌다"
    assert sess["coins_earned"] == 10
    assert sess["coins"] == 110  # seed 100 + 10

    student = seed_org["student"]
    db.refresh(student)
    assert student.coins == 110
    tx = (
        db.query(CoinTransaction)
        .filter(CoinTransaction.student_id == student.id, CoinTransaction.amount == 10)
        .first()
    )
    assert tx is not None, "코인 트랜잭션이 남아야 한다"

    # 학습 시간: 위젯이 보낸 behavior.solve_time_ms가 학습기록에 실려야
    # 학생홈 '이번 주 학습 시간'·요일별 그래프가 0분이 되지 않는다.
    att = db.query(LearningAttempt).filter(LearningAttempt.student_id == student.id).first()
    assert att is not None
    assert att.solve_time_ms == 5321, f"위젯 경로 학습시간이 0으로 저장됨: {att.solve_time_ms}"


def test_widget_verify_without_auth_no_coins(client, db, seed_org):
    """토큰 없는 verify는 채점만 — 코인 미적립(무음)이 서버 규약."""
    _first_party_key(db)
    ch, answer = _dictation_challenge()
    r = client.post(
        "/api/v1/captcha/v1/verify",
        json={"challenge_token": ch["challenge_token"], "answer": answer},
        headers={"X-Site-Key": "ck_edu_testfp"},
    )
    assert r.status_code == 200
    assert r.json().get("session") is None


def _seed_shop(db):
    from app.models import ShopItem

    rows = {}
    for category, items in D.SHOP_CATALOG.items():
        for i, item in enumerate(items):
            row = ShopItem(
                category=category, name=item["name"], icon=item["icon"],
                price=item["price"], order_no=i,
            )
            db.add(row)
            db.flush()
            rows[(category, item["key"])] = row
    db.commit()
    return rows


def test_shop_purchase_deducts_and_owns(client, db, seed_org):
    """상점 구매: 코인 차감 + 보유 반영(지갑 재조회 시 owned 키 포함)."""
    rows = _seed_shop(db)
    tok = _student_token(client)
    headers = {"Authorization": f"Bearer {tok}"}

    # 카탈로그의 유료 아이템 하나(왕관 120냥이면 seed 100으로 부족 → 저렴한 것 선택)
    cheap = min(
        (it for (cat, _k), it in rows.items() if it.price and it.price <= 100),
        key=lambda it: it.price,
    )
    r = client.post(
        "/api/v1/students/me/shop/purchase", json={"item_id": cheap.id}, headers=headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["coins"] == 100 - cheap.price  # 차감 즉시 반영

    w = client.get("/api/v1/students/me/wallet", headers=headers).json()
    assert w["coins"] == 100 - cheap.price
    all_owned = [k for keys in w["owned"].values() for k in keys]
    key_of = next(k for (cat, k), it in rows.items() if it.id == cheap.id)
    assert key_of in all_owned, f"구매 아이템이 보유 목록에 없다: {w['owned']}"

    # 중복 구매는 409 (코인 재차감 없음)
    r2 = client.post(
        "/api/v1/students/me/shop/purchase", json={"item_id": cheap.id}, headers=headers
    )
    assert r2.status_code == 409

    # 잔액 부족 구매는 400, 잔액 불변
    pricey = next(it for (_c, _k), it in rows.items() if it.price > 100)
    r3 = client.post(
        "/api/v1/students/me/shop/purchase", json={"item_id": pricey.id}, headers=headers
    )
    assert r3.status_code == 400
    w2 = client.get("/api/v1/students/me/wallet", headers=headers).json()
    assert w2["coins"] == 100 - cheap.price


def test_shop_purchase_by_design_key(client, db, seed_org):
    """프론트 폴백 카탈로그는 디자인 키('crown' 등)로 구매 — 키 조회도 동작해야 한다."""
    _seed_shop(db)
    tok = _student_token(client)
    headers = {"Authorization": f"Bearer {tok}"}
    r = client.post(
        "/api/v1/students/me/shop/purchase", json={"item_id": "flower"}, headers=headers
    )
    assert r.status_code == 200, r.text
    w = client.get("/api/v1/students/me/wallet", headers=headers).json()
    assert "flower" in w["owned"]["hat"]
