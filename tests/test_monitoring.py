"""운영 모니터링 — 서버 지표 인제스트(토큰 인증) + 대시보드(운영자 전용·self-collect·LLM 집계)."""

from tests.test_captcha_api import _instructor, _ops, auth


def test_monitoring_dashboard_ops_only(client, db):
    otok = _ops(client, db)
    r = client.get("/api/v1/ops/monitoring", headers=auth(otok))
    assert r.status_code == 200, r.text
    body = r.json()
    keys = {s["server_key"] for s in body["servers"]}
    # 기대 서버 5대 중 코드 정의 4키는 데이터 없어도 카드로(no_data) 노출
    assert {"backend", "db", "gpu-stt", "frontend"} <= keys
    # 백엔드는 요청 시 self-collect(psutil) — no_data가 아니고 실측값이 있어야
    be = next(s for s in body["servers"] if s["server_key"] == "backend")
    assert be["no_data"] is False and be["cpu_cores"] >= 1 and be["mem_total_mb"] > 0
    # 추이(그래프) — self-collect가 표본을 append하므로 이력이 최소 1점 있어야
    assert "history" in be and len(be["history"]["cpu"]) >= 1
    # 두 번째 호출이면 표본이 하나 더 쌓인다(append-only 시계열)
    n1 = len(be["history"]["cpu"])
    be2 = next(s for s in client.get("/api/v1/ops/monitoring", headers=auth(otok)).json()["servers"]
               if s["server_key"] == "backend")
    assert len(be2["history"]["cpu"]) >= n1 + 1
    # LLM 집계 필드 존재
    assert "est_cost_usd" in body["llm"] and "providers" in body["llm"]
    # 비운영자(강사)는 403
    itok = _instructor(client, db)
    assert client.get("/api/v1/ops/monitoring", headers=auth(itok)).status_code == 403


def test_metrics_ingest_requires_token(client, db, monkeypatch):
    from app.api.v1.endpoints import monitoring
    from app.models import ServerMetric

    body = {"server_key": "gpu-stt", "label": "GPU STT 워커", "cpu_pct": 9.0,
            "gpu_present": True, "gpu_name": "Tesla T4", "gpu_util_pct": 71.0,
            "gpu_mem_used_mb": 9000, "gpu_mem_total_mb": 15360}

    # 토큰 미설정(기본 빈 값) → 인제스트 비활성(403)
    assert client.post("/api/v1/internal/metrics", json=body).status_code == 403

    # 토큰 설정 후: 일치=200 upsert, 불일치=403
    class _S:
        METRICS_INGEST_TOKEN = "sekret"

    monkeypatch.setattr(monitoring, "get_settings", lambda: _S())
    r = client.post("/api/v1/internal/metrics", json=body, headers={"X-Metrics-Token": "sekret"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert client.post("/api/v1/internal/metrics", json=body,
                       headers={"X-Metrics-Token": "wrong"}).status_code == 403

    # upsert 반영 — 같은 키 재전송은 덮어쓴다(1행 유지)
    row = db.query(ServerMetric).filter(ServerMetric.server_key == "gpu-stt").first()
    assert row is not None and row.gpu_present and row.gpu_name == "Tesla T4"
    client.post("/api/v1/internal/metrics", json={**body, "gpu_util_pct": 88.0},
                headers={"X-Metrics-Token": "sekret"})
    db.expire_all()
    assert db.query(ServerMetric).filter(ServerMetric.server_key == "gpu-stt").count() == 1
    assert db.query(ServerMetric).filter(ServerMetric.server_key == "gpu-stt").first().gpu_util_pct == 88.0
