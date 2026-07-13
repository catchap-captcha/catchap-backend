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
