"""시스템 상태(GET /ops/system) — 클러스터 앱 카드가 ★실측인가.

★이 시험이 지키려는 것
  ① 고정 문자열로 상태를 말하지 않는다. 종전엔 "AI 서버: 미배포"가 하드코딩이라,
     그 앱이 실제로 클러스터에 뜬 뒤에도 영영 '미배포'라고 말했다.
  ② 못 읽은 것을 '고장'으로 위장하지 않는다(수집기 미설정 ≠ 앱 장애).
  ③ 오래된 지표를 '정상'으로 위장하지 않는다 — 수집이 끊기면 그렇다고 말한다.
"""

from datetime import datetime, timedelta

import pytest
from app.core.config import get_settings
from app.core.permissions import Principal, require_ops
from app.main import app
from app.models import ServerMetric

CLUSTER_APPS = {"captcha-api", "behavior-ai", "frontend", "stt-worker"}


@pytest.fixture()
def ops_client(client):
    """운영자 권한을 끼운 클라이언트 — 이 화면은 require_ops 전용이다.
    conftest의 client를 감싸 쓴다(그쪽이 get_db를 SQLite로 갈아끼운다)."""
    app.dependency_overrides[require_ops] = lambda: Principal(kind="user", id="ops-1", role="ops")
    yield client
    app.dependency_overrides.pop(require_ops, None)


def _metric(
    key: str, *, cpu=3.0, mem=40.0, age_sec=10, label=None,
    cores=2, disk_pct=None, disk_used=None, disk_total=None,
) -> ServerMetric:
    return ServerMetric(
        server_key=key,
        label=label or key,
        cpu_pct=cpu,
        cpu_cores=cores,  # 앱 행에서는 '몇 벌인지'를 뜻한다(cluster_metrics._service_snapshots)
        mem_pct=mem,
        disk_pct=disk_pct,
        disk_used_gb=disk_used,
        disk_total_gb=disk_total,
        collected_at=datetime.now() - timedelta(seconds=age_sec),
    )


def _node(name: str, pct: float, *, age_sec=10) -> ServerMetric:
    """노드(서버) 행 — 디스크 카드가 읽는 자료."""
    return _metric(
        f"node:host-{name}", label=f"서버 {name}", age_sec=age_sec,
        disk_pct=pct, disk_used=round(98.2 * pct / 100, 1), disk_total=98.2,
    )


def _cards(client) -> dict[str, dict]:
    r = client.get("/api/v1/ops/system")
    assert r.status_code == 200, r.text
    return {s["name"]: s for s in r.json()["services"]}


def test_no_hardcoded_ai_server_card(ops_client, db, monkeypatch):
    """★핵심 — 점검하지 않으면서 상태를 단언하는 카드가 없어야 한다.

    'ai-server'는 어떤 코드도 실행하지 않고 항상 not_deployed 를 돌려주던 카드다.
    같은 자리를 실측 카드(behavior-ai)가 대신한다."""
    monkeypatch.setattr(get_settings(), "PROMETHEUS_URL", "http://prom:9090")
    cards = _cards(ops_client)
    assert "ai-server" not in cards
    assert CLUSTER_APPS <= set(cards)


def test_missing_collector_is_unknown_not_error(ops_client, db, monkeypatch):
    """수집기가 꺼진 환경(로컬·옛 VM)에서 앱들을 빨갛게 만들지 않는다.

    '못 읽었다'와 '고장났다'는 다른 사실이다. 섞으면 진짜 장애를 못 알아본다."""
    monkeypatch.setattr(get_settings(), "PROMETHEUS_URL", "")
    cards = _cards(ops_client)
    for key in CLUSTER_APPS:
        assert cards[key]["status"] == "unknown"
        assert "PROMETHEUS_URL" in cards[key]["detail"]


def test_fresh_metrics_report_ok_with_real_numbers(ops_client, db, monkeypatch):
    """수집된 값이 그대로 화면 문구가 된다 — 지어내지 않는다."""
    monkeypatch.setattr(get_settings(), "PROMETHEUS_URL", "http://prom:9090")
    db.add(_metric("behavior-ai", cpu=12.5, mem=41.0, cores=3))
    db.commit()

    card = _cards(ops_client)["behavior-ai"]
    assert card["status"] == "ok"
    # ★CPU 는 0816 에 뺐다 — 파드엔 CPU 제한이 없어 판정에 안 쓰이는 숫자였다.
    #   대신 메모리(OOM 경계)와 벌 수(이중화)가 수집값 그대로 나와야 한다.
    assert "41%" in card["detail"] and "3벌 실행 중" in card["detail"]
    # 지표가 아직 없는 앱은 '정상'이 아니라 '모름'이다
    assert _cards(ops_client)["stt-worker"]["status"] == "unknown"


def test_stale_metrics_are_not_reported_as_ok(ops_client, db, monkeypatch):
    """수집이 끊긴 것을 '정상'으로 보여주면 장애가 조용히 지나간다."""
    monkeypatch.setattr(get_settings(), "PROMETHEUS_URL", "http://prom:9090")
    db.add(_metric("captcha-api", age_sec=15 * 60))
    db.commit()

    card = _cards(ops_client)["captcha-api"]
    assert card["status"] == "degraded"
    assert "15분" in card["detail"]


def test_memory_near_limit_is_degraded(ops_client, db, monkeypatch):
    """메모리는 ★제한 대비다 — 넘으면 커널이 파드를 죽인다(OOMKill)."""
    monkeypatch.setattr(get_settings(), "PROMETHEUS_URL", "http://prom:9090")
    db.add(_metric("frontend", mem=91.0))
    db.commit()

    card = _cards(ops_client)["frontend"]
    assert card["status"] == "degraded"
    assert "OOM" in card["detail"]


def test_measured_cards_still_work(ops_client, db, monkeypatch):
    """기존 실측 카드(DB·캡차엔진·SMTP·디스크)가 다 있어야 한다."""
    monkeypatch.setattr(get_settings(), "PROMETHEUS_URL", "http://prom:9090")
    cards = _cards(ops_client)
    # ★>= 1 이었다 — max(1, int(...)) 바닥에 기대던 것이다. 바닥을 없앴으니
    #   "잰 값이 들어 있다"만 확인한다(0.3ms 도 정직한 값이다).
    assert cards["db"]["status"] == "ok" and cards["db"]["latency_ms"] >= 0
    assert cards["captcha-engine"]["status"] in ("ok", "degraded")
    assert "disk" in cards and "smtp" in cards


def test_subject_count_is_counted_not_hardcoded(ops_client, monkeypatch):
    """★과목 수를 세서 말해야 한다 — "6과목" 을 문자열로 박아 두면 늘어나도 6이라고 한다.

    0815 실측: 문제은행이 8과목(IT·안전이 늘었다)인데 화면은 계속 "6과목 정상" 이었다.
    """
    from app.services import subject_banks

    cards = _cards(ops_client)
    detail = cards["captcha-engine"]["detail"]
    if "빈 과목" in detail:
        return  # 빈 과목이 있으면 다른 문구가 나간다 — 이 시험의 대상이 아니다

    real = len(subject_banks.LIVE_SUBJECTS)
    assert f"{real}과목" in detail, f"실제 {real}과목인데 화면은 {detail!r}"


def test_subject_count_follows_the_bank(ops_client, monkeypatch):
    """과목이 늘면 화면 숫자도 따라 늘어야 한다 (하드코딩이면 안 따라온다)."""
    from app.services import subject_banks

    real = set(subject_banks.LIVE_SUBJECTS)
    monkeypatch.setattr(subject_banks, "LIVE_SUBJECTS", frozenset(real | {"__시험과목__"}))
    monkeypatch.setattr(
        subject_banks,
        "playable_pool",
        lambda s: [1] if s == "__시험과목__" else subject_banks.BANKS.get(s, []),
    )
    detail = _cards(ops_client)["captcha-engine"]["detail"]
    if "빈 과목" in detail:
        return
    assert f"{len(real) + 1}과목" in detail, f"과목을 하나 늘렸는데 화면은 {detail!r}"


# ─────────────────────────────────────────────────────────────────────
# 저장공간 카드 — 0816. 그전엔 shutil.disk_usage("/") 로 ★백엔드 파드가
# 우연히 앉은 노드를 쟀다. 파드가 2벌이라 값이 요청마다 튀었고(실측: 60.9%↔61.6%
# 번갈아), 나머지 노드는 아예 안 보여서 그것들이 꽉 차도 「정상」이었다.
# ─────────────────────────────────────────────────────────────────────


def test_disk_card_reports_worst_node_not_one_pod(ops_client, db, monkeypatch):
    """★핵심 — 노드 하나가 아니라 ★가장 많이 쓴 노드를 말해야 한다.

    이 시험이 무는 것: 디스크를 '이 프로세스가 보는 /' 로 재는 구현으로 되돌리면,
    아래 90% 노드를 못 보고 통과해 버린다."""
    monkeypatch.setattr(get_settings(), "PROMETHEUS_URL", "http://prom:9090")
    db.add_all([
        _node("10-0-2-128", 30.6),
        _node("10-0-6-202", 28.2),
        _node("10-0-2-210", 90.4),  # ★한 대만 위험 — 이걸 못 보면 시험이 실패한다
    ])
    db.commit()

    disk = _cards(ops_client)["disk"]
    assert disk["status"] == "degraded", disk
    assert "90.4%" in disk["detail"]
    # 어느 서버인지 말해야 한다 — 그전엔 파드가 자기 노드를 몰라 말할 수 없었다
    assert "10-0-2-210" in disk["detail"]


def test_disk_card_counts_nodes_instead_of_hardcoding(ops_client, db, monkeypatch):
    """대수를 문자열로 박지 않는다 — 「6과목」과 같은 실수를 반복하지 않기 위해."""
    monkeypatch.setattr(get_settings(), "PROMETHEUS_URL", "http://prom:9090")
    db.add_all([_node("a", 10.0), _node("b", 20.0)])
    db.commit()
    assert "서버 2대" in _cards(ops_client)["disk"]["detail"]

    db.add(_node("c", 30.0))
    db.commit()
    assert "서버 3대" in _cards(ops_client)["disk"]["detail"]


def test_disk_card_ignores_stale_nodes(ops_client, db, monkeypatch):
    """수집이 끊긴 노드의 옛 값으로 「위험」이라 말하지 않는다."""
    monkeypatch.setattr(get_settings(), "PROMETHEUS_URL", "http://prom:9090")
    db.add_all([_node("live", 30.0), _node("dead", 99.0, age_sec=9999)])
    db.commit()
    disk = _cards(ops_client)["disk"]
    assert disk["status"] == "ok", disk
    assert "서버 1대" in disk["detail"]


def test_disk_card_unknown_when_no_node_metrics(ops_client, db, monkeypatch):
    """노드 지표가 없으면 '모름' — 0%나 '정상'으로 위장하지 않는다."""
    monkeypatch.setattr(get_settings(), "PROMETHEUS_URL", "http://prom:9090")
    assert _cards(ops_client)["disk"]["status"] == "unknown"


# ─────────────────────────────────────────────────────────────────────
# 앱 카드 — 몇 벌 떠 있나. 화면 안내문은 "각각 2벌씩이라 한 벌이 죽어도
# 이어집니다" 라고 말하는데, ★한 벌만 남아도 카드는 초록 「정상」이었다.
# ─────────────────────────────────────────────────────────────────────


def test_app_card_shows_replica_count(ops_client, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "PROMETHEUS_URL", "http://prom:9090")
    db.add(_metric("captcha-api", mem=20.0, cores=2))
    db.commit()
    card = _cards(ops_client)["captcha-api"]
    assert "2벌 실행 중" in card["detail"], card
    assert card["status"] == "ok"


def test_single_replica_is_degraded(ops_client, db, monkeypatch):
    """★이중화가 깨진 것을 이 화면에서 볼 수 있어야 한다.

    기대 벌 수(2)를 박지 않는다 — 1벌이면 그 자체로 단일 장애점이다."""
    monkeypatch.setattr(get_settings(), "PROMETHEUS_URL", "http://prom:9090")
    db.add(_metric("frontend", mem=5.0, cores=1))
    db.commit()
    card = _cards(ops_client)["frontend"]
    assert card["status"] == "degraded", card
    assert "1벌 실행 중" in card["detail"]
    assert "한 벌뿐" in card["detail"]


def test_app_card_drops_cpu_number(ops_client, db, monkeypatch):
    """CPU 는 판정에 안 쓰이는 숫자였다(파드엔 CPU 제한이 없다).

    실제로 네 카드가 전부 "CPU 0.0%" 였다 — 추세는 모니터링 화면이 본다."""
    monkeypatch.setattr(get_settings(), "PROMETHEUS_URL", "http://prom:9090")
    db.add(_metric("behavior-ai", cpu=0.04, mem=28.0, cores=2))
    db.commit()
    assert "CPU" not in _cards(ops_client)["behavior-ai"]["detail"]


def test_db_latency_is_not_floored_to_1ms(ops_client, db):
    """★max(1, int(...)) 이었다 — 0.3ms 도 "1ms" 로 찍혀 정확한 값처럼 보였다."""
    lat = _cards(ops_client)["db"]["latency_ms"]
    assert isinstance(lat, float), lat
