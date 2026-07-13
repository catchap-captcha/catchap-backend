from app.services import forest_captcha as fc


def test_modified_forest_challenge_has_three_distinct_animals():
    service = fc.ForestCaptchaService(store=fc.InMemoryStore())

    challenge = service.create_challenge()

    assert challenge.theme_id == "forest"
    assert len(challenge.objects) == 3
    assert len({obj.animal for obj in challenge.objects}) == 3
    assert challenge.target_animal in {obj.animal for obj in challenge.objects}
    assert challenge.target_object in fc.OBJECTS
    assert all(obj.start_direction != obj.heading for obj in challenge.objects)


def test_modified_forest_challenge_is_single_use():
    service = fc.ForestCaptchaService(store=fc.InMemoryStore())
    challenge = service.create_challenge()

    assert service.verify(
        challenge.challenge_id,
        challenge.target_object,
        challenge.target_direction,
        theme_id=challenge.theme_id,
    ) is True
    assert service.verify(
        challenge.challenge_id,
        challenge.target_object,
        challenge.target_direction,
        theme_id=challenge.theme_id,
    ) is False


def test_modified_forest_api_hides_answer_and_serves_object_pose(client):
    response = client.post("/api/v1/captcha/forest/challenge")
    assert response.status_code == 200
    body = response.json()

    assert body["theme_id"] == "forest"
    assert len(body["objects"]) == 3
    assert "target_object" not in body
    assert "target_direction" not in body
    assert all("heading" not in obj for obj in body["objects"])

    first = body["objects"][0]
    image = client.get(
        f"/api/v1/captcha/forest/{body['challenge_id']}/reveal/{first['object_id']}"
    )
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.headers["cache-control"] == "no-store"

    record = fc.service.get_active_challenge(body["challenge_id"])
    assert record is not None
    solved = client.post(
        "/api/v1/captcha/forest/verify",
        json={
            "challenge_id": record.challenge_id,
            "theme_id": record.theme_id,
            "selected_object": record.target_object,
            "selected_direction": record.target_direction,
        },
    )
    assert solved.status_code == 200
    assert solved.json()["success"] is True
    assert fc.service.consume_token(solved.json()["captcha_token"]) is True
    assert fc.service.consume_token(solved.json()["captcha_token"]) is False


def test_dbstore_shares_across_worker_instances(db):
    """DBStore로 워커 간 challenge 공유 — 발급 워커와 다른 워커(새 store 인스턴스)에서
    verify해도 정답이 통과해야 한다(멀티워커 '정답이어도 실패' 회귀 방지)."""
    import pytest
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    from app.services import forest_captcha as fc
    from app.db.session import SessionLocal

    # DBStore는 요청 세션이 아니라 앱 설정 DB(SessionLocal)에 직접 붙는다 — 여러 인스턴스가
    # '실제로 공유되는' 저장소여야 검증이 성립하므로 in-memory SQLite로는 대체 불가.
    # 공유 DB가 미가용이면(예: MySQL 미기동 CI) 이 회귀 가드는 skip한다.
    try:
        probe = SessionLocal()
        probe.execute(text("SELECT 1"))
        probe.close()
    except OperationalError as e:
        pytest.skip(f"공유 DB 미가용 — DBStore 크로스워커 테스트 skip: {e}")

    # 워커 A: 별도 서비스 인스턴스로 발급
    svc_a = fc.ForestCaptchaService(store=fc.DBStore())
    rec = svc_a.create_challenge()

    # 워커 B: 완전히 다른 서비스/스토어 인스턴스(다른 프로세스 시뮬)로 검증
    svc_b = fc.ForestCaptchaService(store=fc.DBStore())
    ok = svc_b.verify(rec.challenge_id, rec.target_object, rec.target_direction, rec.theme_id)
    assert ok is True, "다른 워커에서도 정답이 통과해야 한다"

    # 오답은 다른 워커에서도 실패
    rec2 = svc_a.create_challenge()
    bad = svc_b.verify(rec2.challenge_id, rec2.target_object, (rec2.target_direction + 1) % 8, rec2.theme_id)
    assert bad is False

    # 토큰도 워커 간 공유(발급 A → 소비 B)
    tok = svc_a.issue_token()
    assert svc_b.consume_token(tok) is True
    assert svc_b.consume_token(tok) is False  # 단일 사용
