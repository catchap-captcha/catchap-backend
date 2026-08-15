"""운영 모니터링 — 서버 지표 인제스트(토큰 인증) + 대시보드(운영자 전용·self-collect·LLM 집계)."""

from tests.test_captcha_api import _instructor, _ops, auth


def test_monitoring_dashboard_ops_only(client, db):
    """★PROMETHEUS_URL이 없는 환경(로컬·시험)에서는 종전대로 self-collect(psutil)로 동작한다.

    쿠버네티스 배포에서는 클러스터 수집이 이 자리를 대신한다(test_cluster_metrics.py 참고).
    이 시험은 ★하위호환을 지킨다 — 프로메테우스가 없어도 대시보드가 떠야 한다.
    """
    otok = _ops(client, db)
    r = client.get("/api/v1/ops/monitoring", headers=auth(otok))
    assert r.status_code == 200, r.text
    body = r.json()
    keys = {s["server_key"] for s in body["servers"]}
    # 기대 서비스 키는 데이터 없어도 카드로(no_data) 노출
    # ★2026-08-11: 기대 목록이 바뀌었다.
    #   gpu-stt → stt-worker  (클러스터 밖 GPU VM 을 내리고 클러스터가 직접 모은다)
    #   db 는 뺐다 — 관리형이라 에이전트를 못 넣어 ★영영 "없음"으로 남는다.
    #                MySQL 은 Grafana 가 카카오 Metric Export 로 본다.
    #   vm-jump·vm-ops 추가 — 클러스터 밖에 남은 VM 두 대(에이전트 push).
    assert {"backend-api", "frontend", "stt-worker", "vm-jump", "vm-ops"} <= keys
    # 백엔드는 요청 시 self-collect(psutil) — no_data가 아니고 실측값이 있어야
    be = next(s for s in body["servers"] if s["server_key"] == "backend-api")
    assert be["no_data"] is False and be["cpu_cores"] >= 1 and be["mem_total_mb"] > 0
    # 추이(그래프) — self-collect가 표본을 append하므로 이력이 최소 1점 있어야
    assert "history" in be and len(be["history"]["cpu"]) >= 1
    # 두 번째 호출이면 표본이 하나 더 쌓인다(append-only 시계열)
    n1 = len(be["history"]["cpu"])
    be2 = next(s for s in client.get("/api/v1/ops/monitoring", headers=auth(otok)).json()["servers"]
               if s["server_key"] == "backend-api")
    assert len(be2["history"]["cpu"]) >= n1 + 1
    # LLM 집계 필드 존재
    assert "est_cost_usd" in body["llm"] and "providers" in body["llm"]
    # 비운영자(강사)는 403
    itok = _instructor(client, db)
    assert client.get("/api/v1/ops/monitoring", headers=auth(itok)).status_code == 403


def test_dashboard_uses_cluster_metrics_and_puts_nodes_first(client, db, monkeypatch):
    """★PROMETHEUS_URL이 있으면 클러스터 수집을 쓰고, 노드 카드를 서비스보다 앞에 놓는다.

    노드는 EXPECTED_SERVERS에 없다(이름을 카카오가 정하므로 코드에 못 박을 수 없다).
    그래서 정렬을 따로 하지 않으면 ★서비스 뒤로 밀린다 — 바닥을 나중에 보게 된다.
    """
    from app.api.v1.endpoints import monitoring

    class _S:
        PROMETHEUS_URL = "http://prom"
        METRICS_INGEST_TOKEN = ""

    snaps = [
        {"server_key": "node:host-10-0-2-128", "label": "노드 (10.0.2.128)", "host": "10.0.2.128",
         "cpu_pct": 12.0, "cpu_cores": 4, "mem_pct": 40.0, "mem_used_mb": 6553,
         "mem_total_mb": 16383, "disk_pct": 25.0, "disk_used_gb": 25.0, "disk_total_gb": 100.0},
        {"server_key": "backend-api", "label": "백엔드 API (파드 2)", "host": "catchap 네임스페이스",
         "cpu_pct": 0.5, "cpu_cores": 2, "mem_pct": 14.0, "mem_used_mb": 431,
         "mem_total_mb": 3072, "disk_pct": 0.0, "disk_used_gb": 0.0, "disk_total_gb": 0.0},
    ]
    monkeypatch.setattr(monitoring, "get_settings", lambda: _S())
    monkeypatch.setattr(monitoring.cluster_metrics, "collect", lambda _url: snaps)

    otok = _ops(client, db)
    body = client.get("/api/v1/ops/monitoring", headers=auth(otok)).json()
    order = [s["server_key"] for s in body["servers"]]
    assert order[0] == "node:host-10-0-2-128", order
    node = body["servers"][0]
    assert node["no_data"] is False and node["disk_total_gb"] == 100.0
    # 서비스 카드도 클러스터 값으로 채워진다(psutil이 아니라)
    be = next(s for s in body["servers"] if s["server_key"] == "backend-api")
    assert be["mem_total_mb"] == 3072 and be["cpu_cores"] == 2


def test_dashboard_survives_prometheus_outage(client, db, monkeypatch):
    """★프로메테우스가 죽어도 대시보드는 뜬다 — 마지막 값 + '오래됨'으로 정직하게 보인다.

    여기서 500을 주면 운영자는 "모니터링 화면이 고장났다"고 읽는다. 실제로는 수집만
    끊긴 것이고 나머지(LLM 비용·다른 서버)는 멀쩡하다.
    """
    from app.api.v1.endpoints import monitoring
    from app.clients.prometheus_client import PrometheusError

    class _S:
        PROMETHEUS_URL = "http://prom"
        METRICS_INGEST_TOKEN = ""

    def _boom(_url):
        raise PrometheusError("연결 실패")

    monkeypatch.setattr(monitoring, "get_settings", lambda: _S())
    monkeypatch.setattr(monitoring.cluster_metrics, "collect", _boom)

    otok = _ops(client, db)
    r = client.get("/api/v1/ops/monitoring", headers=auth(otok))
    assert r.status_code == 200, r.text
    assert "est_cost_usd" in r.json()["llm"]


def test_monitoring_threshold_alerts(client, db):
    """임계 초과 지표는 경보로 잡힌다 — CPU>90·메모리>85는 경보, 디스크 50%는 아님."""
    from datetime import datetime

    from app.models import ServerMetric

    db.add(ServerMetric(server_key="db", label="DB", cpu_pct=96.0, mem_pct=91.0, disk_pct=50.0,
                        mem_used_mb=1, mem_total_mb=2, collected_at=datetime.now()))
    db.commit()
    otok = _ops(client, db)
    body = client.get("/api/v1/ops/monitoring", headers=auth(otok)).json()
    db_s = next(s for s in body["servers"] if s["server_key"] == "db")
    metrics = {a["metric"] for a in db_s["alerts"]}
    assert "CPU" in metrics and "메모리" in metrics and "디스크" not in metrics
    assert body["alert_count"] >= 2


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


def test_monitoring_hourly_rollup_and_range(client, db, monkeypatch):
    """시간별 롤업 누적(평균=sum/count) + 기간(range)별 소스 분기(6h=raw / 7d=hourly)."""
    from app.api.v1.endpoints import monitoring
    from app.models import ServerMetricHourly

    class _S:
        METRICS_INGEST_TOKEN = "tok"

    monkeypatch.setattr(monitoring, "get_settings", lambda: _S())

    # 같은 서버를 3회 인제스트(같은 시간 버킷에 누적) — cpu 10/20/30, gpu 20/40/60
    for cpu in (10.0, 20.0, 30.0):
        r = client.post(
            "/api/v1/internal/metrics",
            json={"server_key": "gpu-x", "label": "GPU", "cpu_pct": cpu, "mem_pct": 50.0,
                  "gpu_present": True, "gpu_util_pct": cpu * 2},
            headers={"X-Metrics-Token": "tok"},
        )
        assert r.status_code == 200, r.text

    # 롤업 1행에 합계·개수가 누적됐는지(평균은 조회 때 sum/count)
    h = db.query(ServerMetricHourly).filter(ServerMetricHourly.server_key == "gpu-x").first()
    assert h is not None and h.samples == 3
    assert h.cpu_sum == 60.0 and h.mem_sum == 150.0
    assert h.gpu_samples == 3 and h.gpu_sum == 120.0

    otok = _ops(client, db)
    # range=7d → hourly 평균 소스
    body = client.get("/api/v1/ops/monitoring?range=7d", headers=auth(otok)).json()
    gx = next(s for s in body["servers"] if s["server_key"] == "gpu-x")
    assert gx["history"]["range"] == "7d"
    assert gx["history"]["cpu"][-1] == 20.0  # (10+20+30)/3
    assert gx["history"]["gpu"][-1] == 40.0  # (20+40+60)/3

    # range=6h → raw 표본 소스(원시 3점)
    body = client.get("/api/v1/ops/monitoring?range=6h", headers=auth(otok)).json()
    gx = next(s for s in body["servers"] if s["server_key"] == "gpu-x")
    assert gx["history"]["range"] == "6h"
    assert len(gx["history"]["cpu"]) >= 3


def _one_node_snapshot():
    return [{"server_key": "node:a", "label": "노드 A", "host": "10.0.0.1",
             "cpu_pct": 1.0, "cpu_cores": 4, "mem_pct": 2.0, "mem_used_mb": 1,
             "mem_total_mb": 100, "disk_pct": 3.0, "disk_used_gb": 1.0, "disk_total_gb": 10.0}]


def _patch_cluster(monkeypatch, monitoring, snaps):
    class _S:
        PROMETHEUS_URL = "http://prom"
        METRICS_INGEST_TOKEN = ""

    monkeypatch.setattr(monitoring, "get_settings", lambda: _S())
    monkeypatch.setattr(monitoring.cluster_metrics, "collect", lambda _url: snaps)


def test_collect_cluster_retries_once_when_another_collector_inserts_first(db, monkeypatch):
    """★수집기가 여럿이라 같은 server_key 를 동시에 INSERT 할 수 있다.

    배포 직후 첫 주기에는 노드 행이 없어서 4개(파드 2 × uvicorn 워커 2)가 전부
    신선도 관문을 통과한다. 한 행이 유니크에 부딪히면 ★그 주기 수집이 통째로 날아간다.
    되돌리고 한 번 더 하면 행이 이미 있으므로 UPDATE 로 지나간다.
    """
    from sqlalchemy.exc import IntegrityError

    from app.api.v1.endpoints import monitoring

    _patch_cluster(monkeypatch, monitoring, _one_node_snapshot())
    calls = {"n": 0}
    real_commit = db.commit

    def flaky_commit():
        calls["n"] += 1
        if calls["n"] == 1:
            raise IntegrityError("INSERT", {}, Exception("Duplicate entry 'node:a'"))
        return real_commit()

    monkeypatch.setattr(db, "commit", flaky_commit)
    assert monitoring.collect_cluster(db) == 1
    assert calls["n"] == 2, "한 번 부딪히면 다시 시도해야 한다"


def test_collect_cluster_does_not_hide_a_persistent_integrity_error(db, monkeypatch):
    """★두 번째도 부딪히면 감추지 않는다 — 다른 원인일 수 있고, 조용히 넘기면 못 찾는다."""
    from sqlalchemy.exc import IntegrityError

    import pytest

    from app.api.v1.endpoints import monitoring

    _patch_cluster(monkeypatch, monitoring, _one_node_snapshot())

    def always_fails():
        raise IntegrityError("INSERT", {}, Exception("Duplicate entry 'node:a'"))

    monkeypatch.setattr(db, "commit", always_fails)
    with pytest.raises(IntegrityError):
        monitoring.collect_cluster(db)


def test_retired_servers_are_hidden_by_key_or_label(client, db):
    """컷오버로 내린 옛 서버는 현황판에서 뺀다 — ★영영 '오래됨'으로 남지 않게.

    ★기준이 두 가지인 이유
      클러스터 밖 VM 의 server_key 는 그 서버에 심은 에이전트의 SERVER_KEY 환경변수가
      정하므로 코드에 남아 있지 않다(scripts/metrics_agent.py). 이미 내린 뒤에는 물어볼
      곳도 없으니, 화면에 보이는 이름(label)으로도 지정할 수 있어야 정리가 된다.
      NAT 인스턴스 두 대가 실제로 그런 경우였다.
    """
    from datetime import datetime

    from app.models import ServerMetric

    otok = _ops(client, db)
    now = datetime.now()
    db.add_all([
        ServerMetric(server_key="gpu-stt", label="GPU STT 워커", collected_at=now),
        ServerMetric(server_key="vm-nat-a-9f31", label="NAT 2a", collected_at=now),
        ServerMetric(server_key="vm-nat-b-2c07", label="NAT 2b", collected_at=now),
        # 내리지 않은 것까지 지우면 진짜 장애를 감추게 된다 — 이건 남아야 한다
        ServerMetric(server_key="vm-legacy", label="옛 배치 VM", collected_at=now),
    ])
    db.commit()

    body = client.get("/api/v1/ops/monitoring", headers=auth(otok)).json()
    keys = {s["server_key"] for s in body["servers"]}
    labels = {s["label"] for s in body["servers"]}
    assert "gpu-stt" not in keys, "키로 지정한 옛 서버"
    assert {"NAT 2a", "NAT 2b"} & labels == set(), "이름으로 지정한 옛 서버"
    assert "vm-legacy" in keys, "내리지 않은 서버는 그대로 보여야 한다"

def test_expected_servers_have_no_duplicate_keys():
    """★서버마다 키가 달라야 한다 — 겹치면 카드 하나가 여러 서버 값으로 튄다.

    0815 사고: 2-b 짝을 만들며 에이전트 설정을 그대로 복사해서 ★세 대(점프 2b·운영 2a·
    운영 2b)가 같은 server_key("vm-ops")로 보내고 있었다. 같은 키는 최신값으로 덮어쓰므로
    카드 하나가 30초마다 다른 서버 값을 보여줬고, ★점프 2b 가 "빌드·운영 VM"으로 보였다.
    화면만 봐서는 알 수 없다 — 값이 그럴듯하게 나오기 때문이다.
    """
    from app.api.v1.endpoints.monitoring import EXPECTED_SERVERS

    keys = [k for k, _ in EXPECTED_SERVERS]
    labels = [l for _, l in EXPECTED_SERVERS]
    assert len(keys) == len(set(keys)), f"server_key 가 겹칩니다: {keys}"
    assert len(labels) == len(set(labels)), f"화면 이름이 겹칩니다: {labels}"


def test_expected_servers_are_not_hidden_by_retired_list():
    """★기대 목록에 넣은 서버가 RETIRED 명단에 걸려 감춰지면 안 된다.

    0815 사고: 새로 세운 NAT 에 SERVER_KEY="nat-2a" 를 붙였는데, RETIRED_SERVERS 의
    옛 "NAT 2a" 와 ★정규화 비교(대소문자·구분자 무시)에서 같아져 화면에서 감춰졌다.
    에이전트는 정상이고 데이터도 들어오는데 ★카드만 안 보여서 원인 찾기가 어렵다.
    """
    from app.api.v1.endpoints.monitoring import (
        EXPECTED_SERVERS,
        _RETIRED_NORM,
        _norm_server,
    )

    for key, label in EXPECTED_SERVERS:
        assert _norm_server(key) not in _RETIRED_NORM, f"기대 서버인데 키가 은퇴 명단에 있습니다: {key}"
        assert _norm_server(label) not in _RETIRED_NORM, f"기대 서버인데 이름이 은퇴 명단에 있습니다: {label}"
