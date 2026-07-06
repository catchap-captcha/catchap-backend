def test_root_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_v1_health(client):
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    assert res.json()["db"] == "ok"
