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


def _metric(key: str, *, cpu=3.0, mem=40.0, age_sec=10) -> ServerMetric:
    return ServerMetric(
        server_key=key,
        label=key,
        cpu_pct=cpu,
        mem_pct=mem,
        collected_at=datetime.now() - timedelta(seconds=age_sec),
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
    db.add(_metric("behavior-ai", cpu=12.5, mem=41.0))
    db.commit()

    card = _cards(ops_client)["behavior-ai"]
    assert card["status"] == "ok"
    assert "12.5%" in card["detail"] and "41%" in card["detail"]
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
    """기존 실측 카드(DB·캡차엔진·SMTP·디스크)는 그대로여야 한다."""
    monkeypatch.setattr(get_settings(), "PROMETHEUS_URL", "http://prom:9090")
    cards = _cards(ops_client)
    assert cards["db"]["status"] == "ok" and cards["db"]["latency_ms"] >= 1
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
