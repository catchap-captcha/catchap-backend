"""학생 프로필(이름·나이) 수정 — PATCH /students/me/profile."""
from tests.test_lectures import _student_token, auth


def test_update_my_profile(client, db, seed_org):
    tok = _student_token(client, seed_org)
    r = client.patch("/api/v1/students/me/profile", json={"name": "지현", "age": 27}, headers=auth(tok))
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "지현" and r.json()["age"] == 27

    me = client.get("/api/v1/auth/me", headers=auth(tok)).json()
    assert me["name"] == "지현"
    assert me["student"]["age"] == 27


def test_update_my_profile_partial_and_validation(client, db, seed_org):
    tok = _student_token(client, seed_org)
    # 나이만 보내면 이름은 그대로
    before = client.get("/api/v1/auth/me", headers=auth(tok)).json()["name"]
    r = client.patch("/api/v1/students/me/profile", json={"age": 30}, headers=auth(tok))
    assert r.status_code == 200 and r.json()["age"] == 30 and r.json()["name"] == before

    # 빈 이름·범위 밖 나이는 400
    assert client.patch("/api/v1/students/me/profile", json={"name": "   "}, headers=auth(tok)).status_code == 400
    assert client.patch("/api/v1/students/me/profile", json={"age": 0}, headers=auth(tok)).status_code == 400
    assert client.patch("/api/v1/students/me/profile", json={"age": 200}, headers=auth(tok)).status_code == 400
