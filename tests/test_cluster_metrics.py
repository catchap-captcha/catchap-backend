"""클러스터 지표 수집 — 프로메테우스 응답을 우리 스냅샷으로 옮기는 계산이 맞는지.

★여기서 지키려는 것은 두 가지다.
  ① 백분율의 분모가 맞는가 — 파드 메모리는 ★제한 대비여야 한다(OOMKill까지 남은 여유).
     분모를 노드 용량으로 잘못 잡으면 죽기 직전에도 14%로 보인다.
  ② 못 읽었을 때 ★0을 돌려주지 않는가 — 0%는 화면에서 '한가함'으로 읽혀서
     수집이 끊긴 상태를 정상으로 위장한다.
"""

import pytest

from app.clients.prometheus_client import PrometheusError
from app.services import cluster_metrics


def _fake_query(answers: dict[str, list[dict]]):
    """PromQL 문자열 → 미리 정한 응답. 없는 질의는 빈 목록."""

    def _q(expr, *, base_url, timeout=None):  # noqa: ARG001
        return answers.get(expr, [])

    return _q


def _node_answers():
    """노드 2대. 메모리 16GiB 중 2GiB 남음(=87.5% 사용), 디스크 100GiB 중 25GiB 남음(=75%)."""
    gib = 1024**3
    return {
        cluster_metrics._Q_NODE_NAME: [
            {"labels": {"instance": "10.0.2.128:9100", "nodename": "host-10-0-2-128"}, "value": 1.0},
            {"labels": {"instance": "10.0.6.202:9100", "nodename": "host-10-0-6-202"}, "value": 1.0},
        ],
        cluster_metrics._Q_NODE_CPU: [
            {"labels": {"instance": "10.0.2.128:9100"}, "value": 12.34},
            {"labels": {"instance": "10.0.6.202:9100"}, "value": 5.0},
        ],
        cluster_metrics._Q_NODE_CORES: [
            {"labels": {"instance": "10.0.2.128:9100"}, "value": 4.0},
            {"labels": {"instance": "10.0.6.202:9100"}, "value": 4.0},
        ],
        cluster_metrics._Q_NODE_LOAD1: [
            {"labels": {"instance": "10.0.2.128:9100"}, "value": 0.42},
        ],
        cluster_metrics._Q_NODE_MEM_TOTAL: [
            {"labels": {"instance": "10.0.2.128:9100"}, "value": 16.0 * gib},
            {"labels": {"instance": "10.0.6.202:9100"}, "value": 16.0 * gib},
        ],
        cluster_metrics._Q_NODE_MEM_AVAIL: [
            {"labels": {"instance": "10.0.2.128:9100"}, "value": 2.0 * gib},
            {"labels": {"instance": "10.0.6.202:9100"}, "value": 2.0 * gib},
        ],
        cluster_metrics._Q_NODE_FS_SIZE: [
            {"labels": {"instance": "10.0.2.128:9100"}, "value": 100.0 * gib},
            {"labels": {"instance": "10.0.6.202:9100"}, "value": 100.0 * gib},
        ],
        cluster_metrics._Q_NODE_FS_AVAIL: [
            {"labels": {"instance": "10.0.2.128:9100"}, "value": 25.0 * gib},
            {"labels": {"instance": "10.0.6.202:9100"}, "value": 25.0 * gib},
        ],
    }


def test_node_percentages_use_the_right_denominator(monkeypatch):
    """노드 카드 — 메모리는 (전체-남음)/전체, 디스크는 (크기-남음)/크기."""
    monkeypatch.setattr(cluster_metrics, "instant_query", _fake_query(_node_answers()))
    snaps = cluster_metrics.collect("http://prom")

    nodes = [s for s in snaps if s["server_key"].startswith(cluster_metrics.NODE_KEY_PREFIX)]
    assert len(nodes) == 2
    a = next(s for s in nodes if s["server_key"] == "node:host-10-0-2-128")
    assert a["cpu_pct"] == 12.3
    assert a["cpu_cores"] == 4
    assert a["load1"] == 0.42
    assert a["mem_pct"] == 87.5  # 16GiB 중 2GiB 남음
    assert a["mem_total_mb"] == 16 * 1024
    assert a["disk_pct"] == 75.0  # 100GiB 중 25GiB 남음
    assert a["disk_total_gb"] == 100.0
    assert a["host"] == "10.0.2.128"

    # load1이 없는 노드는 ★0이 아니라 None — "0.0 부하"와 "못 읽었다"는 다르다
    b = next(s for s in nodes if s["server_key"] == "node:host-10-0-6-202")
    assert b["load1"] is None


def test_pod_memory_is_measured_against_its_limit(monkeypatch):
    """★파드 메모리 백분율의 분모는 메모리 '제한'이다 — 노드 용량이 아니다.

    파드 2벌이 각각 700MiB를 쓰고 제한이 각각 768MiB면, 합계 1400/1536 = 91.1%.
    이건 OOMKill 직전이라는 뜻이고 CRIT(85%)를 넘겨 경보가 떠야 한다.
    ⚠️분모를 노드(32GiB)로 잡았다면 4.3%로 보여 아무 일도 없는 것처럼 지나간다.
    """
    mib = 1024**2
    answers = _node_answers()
    answers[cluster_metrics._Q_POD_MEM] = [
        {"labels": {"pod": "captcha-api-5444b4d7df-4qjvr"}, "value": 700.0 * mib},
        {"labels": {"pod": "captcha-api-5444b4d7df-hncbl"}, "value": 700.0 * mib},
    ]
    answers[cluster_metrics._Q_POD_MEM_LIMIT] = [
        {"labels": {"pod": "captcha-api-5444b4d7df-4qjvr"}, "value": 768.0 * mib},
        {"labels": {"pod": "captcha-api-5444b4d7df-hncbl"}, "value": 768.0 * mib},
    ]
    answers[cluster_metrics._Q_POD_CPU] = [
        {"labels": {"pod": "captcha-api-5444b4d7df-4qjvr"}, "value": 0.2},
        {"labels": {"pod": "captcha-api-5444b4d7df-hncbl"}, "value": 0.2},
    ]
    monkeypatch.setattr(cluster_metrics, "instant_query", _fake_query(answers))

    snaps = cluster_metrics.collect("http://prom")
    captcha = next(s for s in snaps if s["server_key"] == "captcha-api")
    assert captcha["mem_pct"] == 91.1  # 1400/1536
    assert captcha["mem_used_mb"] == 1400
    assert captcha["mem_total_mb"] == 1536
    assert captcha["cpu_cores"] == 2  # 이 자리는 '몇 벌인지'
    # CPU는 클러스터 전체 코어(8) 대비 — 0.4/8 = 5.0%
    assert captcha["cpu_pct"] == 5.0
    # 제목에는 "(파드 2)" 를 붙이지 않는다 — 쿠버네티스 용어이고, 몇 벌인지는
    # 화면 부제가 cpu_cores 로 "2벌 실행 중" 이라고 이미 보여 준다.
    assert captcha["label"] == "캡차 API"
    assert captcha["cpu_cores"] == 2, "몇 벌인지는 이 값이 진다"


def test_service_with_no_pods_is_omitted_not_zeroed(monkeypatch):
    """파드가 하나도 없는 서비스는 ★카드를 만들지 않는다 → 화면에 '미수집'으로 남는다.

    0으로 채운 카드를 만들면 "떠 있는데 한가하다"로 읽혀서, 서비스가 죽은 것을 감춘다.
    """
    monkeypatch.setattr(cluster_metrics, "instant_query", _fake_query(_node_answers()))
    snaps = cluster_metrics.collect("http://prom")
    keys = {s["server_key"] for s in snaps}
    for group, _ in cluster_metrics.POD_GROUPS:
        assert group not in keys


def test_no_nodes_raises_instead_of_returning_zeros(monkeypatch):
    """노드를 하나도 못 읽으면 ★예외 — 0으로 채운 스냅샷을 돌려주지 않는다."""
    monkeypatch.setattr(cluster_metrics, "instant_query", _fake_query({}))
    with pytest.raises(PrometheusError):
        cluster_metrics.collect("http://prom")


def _gpu_answers():
    """GPU 파드 2개(각자 다른 T4 를 하나씩). util 20%/60%, VRAM 각 3000/14912 MiB."""
    a = dict(_node_answers())
    a[cluster_metrics._Q_POD_MEM] = [
        {"labels": {"pod": "stt-worker-abc-1"}, "value": 1.0},
        {"labels": {"pod": "stt-worker-abc-2"}, "value": 1.0},
        {"labels": {"pod": "frontend-def-1"}, "value": 1.0},
    ]
    a[cluster_metrics._Q_POD_GPU_UTIL] = [
        {"labels": {"exported_pod": "stt-worker-abc-1", "modelName": "Tesla T4"}, "value": 20.0},
        {"labels": {"exported_pod": "stt-worker-abc-2", "modelName": "Tesla T4"}, "value": 60.0},
    ]
    a[cluster_metrics._Q_POD_GPU_MEM_USED] = [
        {"labels": {"exported_pod": "stt-worker-abc-1"}, "value": 3000.0},
        {"labels": {"exported_pod": "stt-worker-abc-2"}, "value": 3000.0},
    ]
    a[cluster_metrics._Q_POD_GPU_MEM_TOTAL] = [
        {"labels": {"exported_pod": "stt-worker-abc-1"}, "value": 14912.0},
        {"labels": {"exported_pod": "stt-worker-abc-2"}, "value": 14912.0},
    ]
    return a


def _card(rows, key):
    return next(r for r in rows if r["server_key"] == key)


def test_gpu_util_is_averaged_and_vram_is_summed(monkeypatch):
    """★사용률은 평균, VRAM 은 합계.

    파드마다 ★다른 GPU 를 하나씩 잡는다. 사용률을 더하면 80% 가 아니라 ★평균 40% 가 맞고,
    VRAM 은 더해야 "얼마나 남았나"가 맞다. 평균/합계를 바꿔 쓰면 화면이 조용히 거짓말한다.
    """
    monkeypatch.setattr(cluster_metrics, "instant_query", _fake_query(_gpu_answers()))
    stt = _card(cluster_metrics.collect("http://x"), "stt-worker")
    assert stt["gpu_present"] is True
    assert stt["gpu_util_pct"] == 40.0          # (20+60)/2 — ★합계 80 이 아니다
    assert stt["gpu_mem_used_mb"] == 6000       # 3000+3000 — ★이쪽은 합계
    assert stt["gpu_mem_total_mb"] == 29824
    assert stt["gpu_name"] == "Tesla T4"


def test_service_without_gpu_is_absent_not_zero(monkeypatch):
    """★GPU 를 안 쓰는 서비스는 gpu_present=False.

    0% 로 채우면 화면에서 "GPU 가 한가하다"로 읽혀 ★GPU 서비스와 구별이 안 된다.
    """
    monkeypatch.setattr(cluster_metrics, "instant_query", _fake_query(_gpu_answers()))
    front = _card(cluster_metrics.collect("http://x"), "frontend")
    assert front["gpu_present"] is False
    assert "gpu_util_pct" not in front


def test_gpu_is_matched_by_exported_pod_not_pod(monkeypatch):
    """🚨★pod 가 아니라 exported_pod 로 묶어야 한다.

    DCGM 이 붙인 pod 라벨은 프로메테우스가 붙이는 pod(스크레이프 대상 = exporter 자신)와
    충돌해서 ★exported_ 접두사가 붙는다. pod 로 물으면 dcgm-exporter 자신이 잡혀서
    STT 카드의 GPU 가 ★영영 비어 있게 된다 — 0815 에 실제로 이 라벨 때문에 헤맸다.
    """
    a = dict(_gpu_answers())
    a[cluster_metrics._Q_POD_GPU_UTIL] = [
        # DCGM 이 아니라 프로메테우스가 붙인 pod 라벨만 있는 경우(=exporter 자신)
        {"labels": {"pod": "dcgm-exporter-xxxxx", "modelName": "Tesla T4"}, "value": 55.0},
    ]
    monkeypatch.setattr(cluster_metrics, "instant_query", _fake_query(a))
    stt = _card(cluster_metrics.collect("http://x"), "stt-worker")
    assert stt["gpu_present"] is False   # ★exporter 자신의 값을 STT 것으로 오인하지 않는다


def test_keys_fit_the_database_column(monkeypatch):
    """server_key는 String(40) — 노드 이름은 카카오가 정하므로 넘칠 수 있다. 잘라서 넣는다."""
    answers = _node_answers()
    answers[cluster_metrics._Q_NODE_NAME] = [
        {"labels": {"instance": "10.0.2.128:9100", "nodename": "host-" + "x" * 80}, "value": 1.0},
    ]
    monkeypatch.setattr(cluster_metrics, "instant_query", _fake_query(answers))
    snaps = cluster_metrics.collect("http://prom")
    assert all(len(s["server_key"]) <= 40 for s in snaps)
    assert all(len(s["label"]) <= 60 for s in snaps)


# ─────────────────────────────────────────────────────────────
# 배경 수집의 겹침 방지 — ★어떤 행을 보고 "방금 걷었다"를 판정하는가
# ─────────────────────────────────────────────────────────────


def _fresh_row(db, key: str, seconds_ago: int = 0):
    from datetime import datetime, timedelta

    from app.models import ServerMetric

    db.add(
        ServerMetric(
            server_key=key,
            label=key,
            collected_at=datetime.now() - timedelta(seconds=seconds_ago),
        )
    )
    db.commit()


def _run_once(db, monkeypatch, interval=30):
    """_collect_cluster_metrics_once를 시험용 세션으로 돌리고, 수집이 일어났는지 돌려준다."""
    import app.db.session as db_session
    from app import main

    called: list[int] = []
    monkeypatch.setattr(db_session, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    monkeypatch.setattr(
        "app.api.v1.endpoints.monitoring.collect_cluster", lambda _db: called.append(1)
    )
    main._collect_cluster_metrics_once(interval)
    return bool(called)


def test_agent_pushed_rows_do_not_block_cluster_collection(db, monkeypatch):
    """★클러스터 밖 에이전트(GPU STT)가 방금 밀어넣었어도 클러스터 수집은 돌아야 한다.

    server_metrics 전체의 최신 시각으로 판정하면, 30초마다 밀어넣는 에이전트 때문에
    ★클러스터 수집이 영원히 건너뛰어진다(노드·파드 카드가 절대 안 갱신된다).
    판정은 ★노드 행(이 수집기만 쓰는 행)으로 해야 한다.
    """
    _fresh_row(db, "gpu-stt", seconds_ago=0)  # 에이전트가 방금 밀어넣음
    assert _run_once(db, monkeypatch) is True


def test_recent_node_row_skips_duplicate_collection(db, monkeypatch):
    """노드 행이 방금 갱신됐으면 건너뛴다 — 파드 2벌이 같은 표본을 두 번 쌓지 않게."""
    _fresh_row(db, "node:host-10-0-2-128", seconds_ago=1)
    assert _run_once(db, monkeypatch) is False


def test_stale_node_row_triggers_collection(db, monkeypatch):
    """노드 행이 주기를 넘겨 오래됐으면 걷는다."""
    _fresh_row(db, "node:host-10-0-2-128", seconds_ago=60)
    assert _run_once(db, monkeypatch) is True

def test_node_card_says_what_the_server_does(monkeypatch):
    """★노드 카드 이름은 "무엇을 하는 서버인가"를 말해야 한다.

    그전에는 "노드 (10.0.2.128)" 이었다. IP 만으로는 운영자가 ★무슨 서버인지 알 수 없고,
    쿠버네티스를 모르는 사람에게 '노드'라는 말 자체가 뜻을 주지 못한다.
    """
    a = dict(_node_answers())
    a[cluster_metrics._Q_NODE_GPU_CAP] = [
        {"labels": {"node": "host-10-0-2-210"}, "value": 1.0},
    ]
    a[cluster_metrics._Q_NODE_NAME] = [
        {"labels": {"instance": "10.0.2.128:9100", "nodename": "host-10-0-2-128"}, "value": 1.0},
        {"labels": {"instance": "10.0.2.210:9100", "nodename": "host-10-0-2-210"}, "value": 1.0},
        {"labels": {"instance": "10.0.6.202:9100", "nodename": "host-10-0-6-202"}, "value": 1.0},
    ]
    monkeypatch.setattr(cluster_metrics, "instant_query", _fake_query(a))
    labels = {r["server_key"]: r["label"] for r in cluster_metrics.collect("http://x")}
    assert labels["node:host-10-0-2-128"] == "서비스 서버 · 일반 · 2-a (10.0.2.128)"
    assert labels["node:host-10-0-2-210"] == "서비스 서버 · GPU · 2-a (10.0.2.210)"   # ★GPU 를 밝힌다
    assert labels["node:host-10-0-6-202"] == "서비스 서버 · 일반 · 2-b (10.0.6.202)"  # ★영역도


def test_node_label_survives_missing_gpu_metric(monkeypatch):
    """★GPU 지표가 없어도 카드가 깨지지 않는다.

    DCGM/kube-state-metrics 가 없는 환경도 있다. 이름이 덜 친절해질 뿐 수집은 계속돼야 한다.
    """
    monkeypatch.setattr(cluster_metrics, "instant_query", _fake_query(_node_answers()))
    rows = [r for r in cluster_metrics.collect("http://x") if r["server_key"].startswith("node:")]
    assert rows and all(r["label"].startswith("서비스 서버 · 일반") for r in rows)


def test_unknown_subnet_gets_no_fake_zone():
    """★모르는 대역에 억지로 영역을 붙이지 않는다 — 틀린 위치는 없는 것만 못하다."""
    assert cluster_metrics._zone_of("172.16.0.9") == ""
    assert cluster_metrics._zone_of("10.0.6.202") == "2-b"


# ── 노드 카드의 GPU (0815) ─────────────────────────────────────────────────
# 이름에 "GPU" 라고 써 놓고 카드에는 "GPU 없음" 이라고 하고 있었다(화면 실측).
# DCGM 지표에는 노드 이름이 없어서(Hostname 이 DCGM 파드 이름) 그냥은 이어지지 않는다.
def _node_gpu_answers():
    """기존 노드 2대(일반)에 ★GPU 노드 1대를 더한다 — 실제 구성과 같은 모양."""
    gib = 1024**3
    a = _node_answers()
    g = "10.0.2.210:9100"
    a[cluster_metrics._Q_NODE_NAME].append(
        {"labels": {"instance": g, "nodename": "host-10-0-2-210"}, "value": 1.0}
    )
    a[cluster_metrics._Q_NODE_CPU].append({"labels": {"instance": g}, "value": 3.0})
    a[cluster_metrics._Q_NODE_CORES].append({"labels": {"instance": g}, "value": 16.0})
    a[cluster_metrics._Q_NODE_MEM_TOTAL].append({"labels": {"instance": g}, "value": 62.0 * gib})
    a[cluster_metrics._Q_NODE_MEM_AVAIL].append({"labels": {"instance": g}, "value": 58.0 * gib})
    a[cluster_metrics._Q_NODE_FS_SIZE].append({"labels": {"instance": g}, "value": 100.0 * gib})
    a[cluster_metrics._Q_NODE_FS_AVAIL].append({"labels": {"instance": g}, "value": 35.0 * gib})
    # 이름표가 "GPU" 가 되려면 이 질의가 그 노드를 짚어 줘야 한다
    a[cluster_metrics._Q_NODE_GPU_CAP] = [
        {"labels": {"node": "host-10-0-2-210"}, "value": 1.0},
    ]
    a[cluster_metrics._Q_DCGM_POD_NODE] = [
        {"labels": {"pod": "dcgm-exporter-aaa", "node": "host-10-0-2-210"}, "value": 1.0},
    ]
    a[cluster_metrics._Q_NODE_GPU_UTIL] = [
        {"labels": {"Hostname": "dcgm-exporter-aaa", "modelName": "Tesla T4"}, "value": 40.0},
    ]
    a[cluster_metrics._Q_NODE_GPU_MEM_USED] = [
        {"labels": {"Hostname": "dcgm-exporter-aaa"}, "value": 3000.0},
    ]
    a[cluster_metrics._Q_NODE_GPU_MEM_FREE] = [
        {"labels": {"Hostname": "dcgm-exporter-aaa"}, "value": 11912.0},
    ]
    return a


def test_gpu_node_card_reports_its_gpu(monkeypatch):
    """★이름이 "GPU" 인 노드는 카드에서도 GPU 를 말해야 한다 (제목과 내용이 어긋나지 않게)."""
    monkeypatch.setattr(cluster_metrics, "instant_query", _fake_query(_node_gpu_answers()))
    got = {s["server_key"]: s for s in cluster_metrics.collect("http://x")}

    gpu_a = got["node:host-10-0-2-210"]
    assert "GPU" in gpu_a["label"], "이름에 GPU 가 들어가는 노드인지 먼저 확인"
    assert gpu_a["gpu_present"] is True, "이름은 GPU 인데 카드가 'GPU 없음' 이면 안 된다"
    assert gpu_a["gpu_name"] == "Tesla T4"
    assert gpu_a["gpu_util_pct"] == 40.0
    assert gpu_a["gpu_mem_used_mb"] == 3000
    assert gpu_a["gpu_mem_total_mb"] == 14912, "쓴 것 + 남은 것"


def test_plain_node_card_has_no_gpu(monkeypatch):
    """일반 노드는 그대로 'GPU 없음' 이어야 한다 (다 붙여 버리면 그것도 거짓이다)."""
    monkeypatch.setattr(cluster_metrics, "instant_query", _fake_query(_node_gpu_answers()))
    got = {s["server_key"]: s for s in cluster_metrics.collect("http://x")}
    plain = got["node:host-10-0-2-128"]
    assert "일반" in plain["label"]
    assert plain["gpu_present"] is False


def test_node_gpu_is_skipped_when_dcgm_is_missing(monkeypatch):
    """DCGM 이 없거나 죽어도 ★노드 카드 자체는 나와야 한다 (GPU 만 빠진다)."""
    a = _node_gpu_answers()  # GPU 노드는 있는데
    for q in (cluster_metrics._Q_DCGM_POD_NODE, cluster_metrics._Q_NODE_GPU_UTIL,
              cluster_metrics._Q_NODE_GPU_MEM_USED, cluster_metrics._Q_NODE_GPU_MEM_FREE):
        a.pop(q, None)  # ★DCGM 이 죽어 지표만 없는 상황
    monkeypatch.setattr(cluster_metrics, "instant_query", _fake_query(a))
    got = {s["server_key"]: s for s in cluster_metrics.collect("http://x")}
    assert got["node:host-10-0-2-210"]["gpu_present"] is False
    assert got["node:host-10-0-2-210"]["cpu_cores"] > 0, "다른 지표는 그대로 살아 있어야 한다"


def test_apps_by_node_groups_pods_by_display_name(monkeypatch):
    """★"이 서버에서 무엇이 도는가" — 파드 이름 뒤 무작위를 떼고 표시 이름으로 묶는다."""
    answers = {
        cluster_metrics._Q_POD_NODE: [
            {"labels": {"pod": "backend-api-677b65d757-vwvz2", "node": "n1"}, "value": 1.0},
            {"labels": {"pod": "backend-api-677b65d757-abcde", "node": "n1"}, "value": 1.0},
            {"labels": {"pod": "stt-worker-f9c74b456-9cd2p", "node": "n1"}, "value": 1.0},
            {"labels": {"pod": "frontend-78c6478bf8-6k46s", "node": "n2"}, "value": 1.0},
            {"labels": {"pod": "backend-migrate-xkgtw", "node": "n1"}, "value": 1.0},
            {"labels": {"pod": "somebody-else", "node": "n2"}, "value": 1.0},
            {"labels": {"pod": "frontend-78c6478bf8-zzzzz"}, "value": 1.0},  # 노드 없음
        ],
    }
    monkeypatch.setattr(cluster_metrics, "instant_query", _fake_query(answers))
    got = cluster_metrics.apps_by_node("http://x")

    assert got["n1"] == ["백엔드 API", "STT 워커"], "POD_GROUPS 순서 그대로, 같은 앱은 한 번만"
    assert got["n2"] == ["프론트"], "모르는 파드는 안 센다"
    assert "backend-migrate" not in str(got), "일회성 작업 파드는 앱이 아니다"


def test_node_card_lists_the_apps_on_it(monkeypatch):
    """노드 카드가 그 서버 위의 앱을 들고 온다 — 서버와 앱이 이어져 보이게."""
    a = _node_gpu_answers()
    a[cluster_metrics._Q_POD_NODE] = [
        {"labels": {"pod": "backend-api-1-a", "node": "host-10-0-2-210"}, "value": 1.0},
        {"labels": {"pod": "stt-worker-1-a", "node": "host-10-0-2-210"}, "value": 1.0},
        {"labels": {"pod": "frontend-1-a", "node": "host-10-0-2-128"}, "value": 1.0},
    ]
    monkeypatch.setattr(cluster_metrics, "instant_query", _fake_query(a))
    got = {s["server_key"]: s for s in cluster_metrics.collect("http://x")}
    assert got["node:host-10-0-2-210"]["apps"] == ["백엔드 API", "STT 워커"]
    assert got["node:host-10-0-2-128"]["apps"] == ["프론트"]
    assert got["node:host-10-0-6-202"]["apps"] == [], "아무것도 없으면 빈 목록"
