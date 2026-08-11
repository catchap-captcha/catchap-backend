"""운영 모니터링 — 서버별 자원(CPU/메모리/디스크/GPU) + LLM API 사용량·비용.

값이 들어오는 길 세 가지. 어느 길로 왔든 ★같은 표(server_metrics)에 쌓이므로 화면은
출처를 모른다.

- ★클러스터 수집 : 백엔드가 프로메테우스에 물어 노드·서비스 지표를 걷는다(쿠버네티스 배포).
  기동 시 배경 작업으로 30초마다 돌고, 대시보드를 열 때도 한 번 걷는다.
- POST /internal/metrics : 쿠버네티스 밖 VM의 에이전트(scripts/metrics_agent.py)가
  X-Metrics-Token으로 밀어넣는다. ★GPU STT 워커처럼 클러스터 밖에 있는 것이 여기로 온다.
- GET /ops/monitoring : 운영자 대시보드. 최신값 + 신선도(오래됨) + LLM 사용량 집계.

★왜 클러스터 수집이 생겼나 (0805)
  옛 환경은 VM 5대라 에이전트를 심을 대상이 고정이었다. 새 환경은 파드가 수시로 생겼다
  사라진다. 게다가 백엔드가 자기를 psutil로 재던 방식은 파드가 2벌이 되면서 ★둘이 같은
  행을 덮어썼고(요청마다 값이 튐), 컨테이너 안 psutil은 cgroup을 안 봐서 ★파드가 아니라
  노드를 재고 있었다. 자세한 것은 app/services/cluster_metrics.py 머리말.

설계 주석(팀 학습용): 최신 1행/서버(현황판) + raw 표본 48h + 시간별 롤업 35일.
"""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.clients.prometheus_client import PrometheusNotConfiguredError
from app.core.config import get_settings
from app.core.permissions import Principal, require_ops
from app.db.session import get_db
from app.models import AiModelConfig, ServerMetric, ServerMetricHourly, ServerMetricSample
from app.services import cluster_metrics, host_metrics

_log = logging.getLogger(__name__)

router = APIRouter()

# 추이 — raw 표본(30초)은 단기(48h)만 보존하고, 장기(주/월)는 시간별 롤업(server_metric_hourly)을
# 쓴다. raw를 월간까지 보관하면 서버당 8만 행이 넘어 비대해지므로, 시간당 1행으로 압축한다.
RAW_RETENTION_HOURS = 48    # raw 표본 보존창(6h/24h 그래프용)
HOURLY_RETENTION_DAYS = 35  # 시간별 롤업 보존창(7d/30d 그래프용, 여유 5일)
HISTORY_POINTS = 120  # 그래프에 내릴 최대 점(초과분은 균등 다운샘플)
# 기간 선택 → (소스, 창 크기). raw는 시간(hours) 단위, hourly는 일(days) 단위.
RANGES: dict[str, tuple[str, int]] = {
    "6h": ("raw", 6),
    "24h": ("raw", 24),
    "7d": ("hourly", 7),
    "30d": ("hourly", 30),
}
DEFAULT_RANGE = "6h"

# 임계 경보 기준(%) — 이 이상이면 '경보'. 색 임계(60/85)보다 높은 '위험' 티어(운영 개입 신호).
# 서버에 두는 이유: 화면 강조뿐 아니라 향후 알림(웹훅·메일) 훅이 같은 기준을 쓰게 하려고.
CRIT = {"CPU": 90.0, "메모리": 85.0, "디스크": 90.0, "GPU": 90.0, "VRAM": 90.0}


def _alerts(row: ServerMetric) -> list[dict]:
    """이 서버의 임계 초과 지표 목록 — 각 {metric, value, threshold}."""
    out: list[dict] = []

    def chk(metric: str, value: float | None) -> None:
        if value is not None and value >= CRIT[metric]:
            out.append({"metric": metric, "value": round(value, 1), "threshold": CRIT[metric]})

    chk("CPU", row.cpu_pct)
    chk("메모리", row.mem_pct)
    chk("디스크", row.disk_pct)
    chk("GPU", row.gpu_util_pct)
    if row.gpu_mem_total_mb:
        chk("VRAM", (row.gpu_mem_used_mb or 0) / row.gpu_mem_total_mb * 100)
    return out

# 대시보드가 보여줄 '기대 서비스' — 데이터가 아직 없어도 카드로 노출(미수집 표시).
#
# ★노드는 여기에 없다. 노드 이름은 카카오가 정하고 교체될 수 있어 코드에 못 박으면 안 된다.
#   수집기가 "node:" 접두사를 붙여 넣고, 화면에서는 ★서비스보다 앞에 놓는다(아래 정렬).
#
# ⚠️옛 VM 시절 키("backend"·"ai")는 여기서 빠졌다 — 새 환경엔 그런 서버가 없다.
#   그 행들은 지워지지 않고 '오래됨'으로 뒤에 남는다. ★그게 정직하다(옛 서버 에이전트가
#   멈춰 있다는 사실 그대로). 컷오버로 옛 서버를 내린 뒤에 지우면 된다.
EXPECTED_SERVERS: list[tuple[str, str]] = [
    ("backend-api", "백엔드 API"),
    ("frontend", "프론트"),
    ("captcha-api", "캡차 API"),
    ("behavior-ai", "행동 AI"),
    ("stt-worker", "STT 워커"),  # ★2026-08-11: 클러스터로 옮김(옛 gpu-stt VM 은 내렸다)
    # ★클러스터 ★밖 VM 두 대 — 각 서버의 에이전트(scripts/metrics_agent.py)가 밀어 넣는다.
    #   0806 STT 장애의 원인이 「클러스터 밖 GPU VM 의 디스크가 0바이트」였다.
    #   그런 서버가 안 보이면 같은 일이 또 조용히 난다.
    ("vm-jump", "점프서버"),
    ("vm-ops", "빌드·운영 VM"),
]
# ⚠️DB(MySQL)는 여기 없다. 관리형이라 에이전트를 못 넣는다.
#   ★대신 Grafana 가 카카오클라우드 Metric Export 로 본다(kc-mysql 데이터소스).
#   여기에 넣어 두면 ★영영 "없음"으로 빨갛게 남아 진짜 고장과 구별이 안 된다.
STALE_AFTER_SEC = 120  # 이 시간 넘게 갱신 없으면 '오래됨'(수집 중단 의심)


class MetricIn(BaseModel):
    server_key: str
    label: str
    host: str | None = None
    cpu_pct: float = 0.0
    cpu_cores: int = 0
    load1: float | None = None
    mem_pct: float = 0.0
    mem_used_mb: int = 0
    mem_total_mb: int = 0
    disk_pct: float = 0.0
    disk_used_gb: float = 0.0
    disk_total_gb: float = 0.0
    gpu_present: bool = False
    gpu_name: str | None = None
    gpu_util_pct: float | None = None
    gpu_mem_used_mb: int | None = None
    gpu_mem_total_mb: int | None = None
    collected_at: datetime | None = None


_METRIC_FIELDS = (
    "label", "host", "cpu_pct", "cpu_cores", "load1", "mem_pct", "mem_used_mb",
    "mem_total_mb", "disk_pct", "disk_used_gb", "disk_total_gb", "gpu_present",
    "gpu_name", "gpu_util_pct", "gpu_mem_used_mb", "gpu_mem_total_mb", "collected_at",
)


def _upsert(db: Session, snap: dict) -> ServerMetric:
    """server_key 기준 upsert(최신 1행) + 시계열 표본 append(추이 그래프)."""
    key = snap["server_key"]
    row = db.query(ServerMetric).filter(ServerMetric.server_key == key).first()
    if row is None:
        row = ServerMetric(server_key=key)
        db.add(row)
    for f in _METRIC_FIELDS:
        if f in snap and snap[f] is not None:
            setattr(row, f, snap[f])
    # collected_at은 각 서버가 측정한 시각을 그대로 쓴다(백엔드 시각으로 덮지 않는다) —
    # 5대 VM이 전부 KST로 통일돼 있어 서버별 시각을 그대로 비교해도 신선도가 어긋나지 않는다.
    # (과거 GPU만 어긋난 건 그 VM의 에이전트 프로세스가 OS tz 변경 전 UTC를 물고 있어서였고,
    #  재부팅/재시작으로 KST 프로세스가 뜨면 자연히 정렬된다. 서버 tz 자체가 정본.)
    ts = snap.get("collected_at") or datetime.now()
    if snap.get("collected_at") is None:
        row.collected_at = ts
    # 추이용 raw 표본 1개 append(가벼운 3지표만) — 단기(48h) 그래프용
    cpu = float(snap.get("cpu_pct") or 0.0)
    mem = float(snap.get("mem_pct") or 0.0)
    gpu = snap.get("gpu_util_pct")
    db.add(ServerMetricSample(
        server_key=key, cpu_pct=cpu, mem_pct=mem, gpu_util_pct=gpu, collected_at=ts,
    ))
    # 시간별 롤업 누적(장기 주/월 그래프용) — 현재 시간 버킷 1행에 합계·개수를 running으로 더한다.
    # 평균은 조회 때 sum/count로 낸다(테이블에 평균을 두면 재계산이 어렵다). gpu는 없는 서버가
    # 있어 분모(gpu_samples)를 따로 센다.
    hour = ts.replace(minute=0, second=0, microsecond=0)
    hrow = (
        db.query(ServerMetricHourly)
        .filter(ServerMetricHourly.server_key == key, ServerMetricHourly.hour == hour)
        .first()
    )
    if hrow is None:
        hrow = ServerMetricHourly(server_key=key, hour=hour)
        db.add(hrow)
    hrow.samples = (hrow.samples or 0) + 1
    hrow.cpu_sum = (hrow.cpu_sum or 0.0) + cpu
    hrow.mem_sum = (hrow.mem_sum or 0.0) + mem
    if gpu is not None:
        hrow.gpu_sum = (hrow.gpu_sum or 0.0) + float(gpu)
        hrow.gpu_samples = (hrow.gpu_samples or 0) + 1
    return row


def _prune(db: Session) -> None:
    """보존창 밖 정리 — raw 표본(48h)과 시간별 롤업(35일) 각각. 무한 증가 방지."""
    raw_cutoff = datetime.now() - timedelta(hours=RAW_RETENTION_HOURS)
    db.query(ServerMetricSample).filter(ServerMetricSample.collected_at < raw_cutoff).delete(
        synchronize_session=False
    )
    hourly_cutoff = datetime.now() - timedelta(days=HOURLY_RETENTION_DAYS)
    db.query(ServerMetricHourly).filter(ServerMetricHourly.hour < hourly_cutoff).delete(
        synchronize_session=False
    )


def collect_cluster(db: Session) -> int:
    """프로메테우스에서 노드·서비스 지표를 걷어 표에 쓴다 → 걷은 대상 수.

    대시보드 요청(아래)과 기동 시 배경 작업(app/main.py)이 ★같은 이 함수를 쓴다.
    수집 주기가 두 군데로 갈라지면 하나만 고쳐서 어긋나기 때문이다.

    설정이 없거나(쿠버네티스가 아닌 배포) 못 읽으면 예외를 그대로 올린다 — 무엇을 할지는
    호출부가 정한다. ★여기서 0으로 채워 '수집 정상'처럼 보이게 만들지 않는다.

    ★★한 번 되돌리고 다시 하는 이유 (0805 프로덕션 실측)
      수집기가 여럿이다 — uvicorn --workers 2 × 파드 2벌 = ★4개. 배포 직후 첫 주기에는
      노드 행이 아직 없어서 넷 다 신선도 관문을 통과하고 ★같은 server_key 를 INSERT 한다.
        (1062, "Duplicate entry 'node:host-10-0-6-202' for key 'ix_server_metrics_key'")
      한 행이 부딪히면 트랜잭션 전체가 죽어 ★그 주기의 수집이 통째로 날아간다.
      되돌리고 한 번 더 하면 이번엔 행이 있으므로 _upsert 가 UPDATE 로 지나간다.
    """
    snaps = cluster_metrics.collect(get_settings().PROMETHEUS_URL)
    for attempt in (1, 2):
        try:
            for snap in snaps:
                _upsert(db, snap)
            _prune(db)
            db.commit()
            return len(snaps)
        except IntegrityError:
            db.rollback()
            if attempt == 2:
                raise  # 두 번째도 부딪히면 다른 원인이다 — 감추지 않는다
            _log.info("클러스터 지표: 다른 수집기와 겹쳐 다시 시도합니다")
    return 0  # 여기 오지 않는다(위에서 return 아니면 raise)


def _downsample(rows: list, points: int) -> list:
    """HISTORY_POINTS 초과 시 균등 다운샘플(그래프 점 수 상한)."""
    if len(rows) > points:
        step = len(rows) / points
        return [rows[int(i * step)] for i in range(points)]
    return rows


def _history(db: Session, server_key: str, range_key: str) -> dict:
    """서버별 추이 배열 — 기간에 따라 raw 표본(단기) 또는 시간별 롤업 평균(장기)을 낸다.

    6h/24h는 raw(30초)를, 7d/30d는 hourly 롤업(sum/count 평균)을 쓴다. 둘 다 시간순으로,
    HISTORY_POINTS를 넘으면 균등 다운샘플한다. 응답 형태는 동일(t/cpu/mem/gpu)라 프론트는
    소스를 몰라도 같은 코드로 그린다."""
    src, span = RANGES.get(range_key, RANGES[DEFAULT_RANGE])
    if src == "raw":
        cutoff = datetime.now() - timedelta(hours=span)
        rows = _downsample(
            db.query(ServerMetricSample)
            .filter(
                ServerMetricSample.server_key == server_key,
                ServerMetricSample.collected_at >= cutoff,
            )
            .order_by(ServerMetricSample.collected_at)
            .all(),
            HISTORY_POINTS,
        )
        return {
            "range": range_key,
            "t": [r.collected_at.isoformat(timespec="seconds") for r in rows],
            "cpu": [round(r.cpu_pct, 1) for r in rows],
            "mem": [round(r.mem_pct, 1) for r in rows],
            "gpu": [round(r.gpu_util_pct, 1) if r.gpu_util_pct is not None else None for r in rows],
        }
    # hourly 롤업 — 시간 버킷별 평균(sum/count). gpu는 gpu_samples>0인 버킷만 값이 있다.
    cutoff = datetime.now() - timedelta(days=span)
    rows = _downsample(
        db.query(ServerMetricHourly)
        .filter(ServerMetricHourly.server_key == server_key, ServerMetricHourly.hour >= cutoff)
        .order_by(ServerMetricHourly.hour)
        .all(),
        HISTORY_POINTS,
    )
    return {
        "range": range_key,
        "t": [r.hour.isoformat(timespec="seconds") for r in rows],
        "cpu": [round(r.cpu_sum / r.samples, 1) if r.samples else 0.0 for r in rows],
        "mem": [round(r.mem_sum / r.samples, 1) if r.samples else 0.0 for r in rows],
        "gpu": [round(r.gpu_sum / r.gpu_samples, 1) if r.gpu_samples else None for r in rows],
    }


@router.post("/internal/metrics")
def ingest_metrics(
    payload: MetricIn,
    x_metrics_token: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """VM 에이전트 인제스트 — 공유 시크릿(X-Metrics-Token) 일치해야 받는다.

    토큰 미설정(빈 값)이면 인제스트 비활성(403) — 실서비스에서 인증 없이 아무나 지표를
    조작하는 구멍을 막는다. 백엔드 자신은 이 경로가 아니라 self-collect로 채운다."""
    secret = get_settings().METRICS_INGEST_TOKEN
    if not secret or x_metrics_token != secret:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="메트릭 인제스트 인증 실패")
    _upsert(db, payload.model_dump())
    _prune(db)
    db.commit()
    return {"ok": True}


def _row_out(row: ServerMetric | None, key: str, label: str) -> dict:
    if row is None:
        return {"server_key": key, "label": label, "no_data": True}
    age = (datetime.now() - row.collected_at).total_seconds() if row.collected_at else None
    stale = age is not None and age > STALE_AFTER_SEC
    alerts = _alerts(row)
    if stale:
        alerts = [{"metric": "수집", "value": None, "threshold": None}, *alerts]
    return {
        "server_key": row.server_key,
        "label": row.label,
        "host": row.host,
        "cpu_pct": round(row.cpu_pct, 1),
        "cpu_cores": row.cpu_cores,
        "load1": round(row.load1, 2) if row.load1 is not None else None,
        "mem_pct": round(row.mem_pct, 1),
        "mem_used_mb": row.mem_used_mb,
        "mem_total_mb": row.mem_total_mb,
        "disk_pct": round(row.disk_pct, 1),
        "disk_used_gb": row.disk_used_gb,
        "disk_total_gb": row.disk_total_gb,
        "gpu_present": row.gpu_present,
        "gpu_name": row.gpu_name,
        "gpu_util_pct": round(row.gpu_util_pct, 1) if row.gpu_util_pct is not None else None,
        "gpu_mem_used_mb": row.gpu_mem_used_mb,
        "gpu_mem_total_mb": row.gpu_mem_total_mb,
        "age_sec": int(age) if age is not None else None,
        "stale": stale,
        "alerts": alerts,
        "no_data": False,
    }


@router.get("/ops/monitoring")
def ops_monitoring(
    range_key: str = Query(default=DEFAULT_RANGE, alias="range"),
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """운영자 모니터링 대시보드 — 서버별 자원 + LLM 사용량·비용.

    열 때 클러스터 지표를 한 번 걷는다(배경 작업이 30초마다 걷지만, 방금 뜬 파드도 바로
    보이게 하려고). 클러스터 밖 서버는 에이전트가 밀어넣은 최신값(없으면 no_data).
    LLM은 AiModelConfig 누적 토큰×단가로 추정 비용 집계."""
    try:
        collect_cluster(db)
    except PrometheusNotConfiguredError:
        # 쿠버네티스가 아닌 배포(로컬 개발·단일 VM) — 종전대로 이 서버만 psutil로 잰다.
        # ⚠️컨테이너 안에서는 노드를 재게 되지만, 그 환경에서는 파드가 1벌이라 문제되지 않는다.
        try:
            _upsert(db, host_metrics.collect("backend-api", "백엔드 API", host="self"))
            _prune(db)
            db.commit()
        except Exception:
            db.rollback()  # 측정 실패해도 대시보드는 나머지로 뜬다(백엔드가 no_data로 보일 수 있음)
    except Exception as exc:
        db.rollback()
        # 화면은 마지막으로 성공한 값으로 뜨고, 시간이 지나면 각 카드가 '오래됨'이 된다.
        # ★그게 "수집이 끊겼다"를 운영자에게 알리는 방식이다(0으로 덮지 않는다).
        _log.warning("클러스터 지표 수집 실패: %s", exc)

    rows = {r.server_key: r for r in db.query(ServerMetric).all()}
    expected_keys = {k for k, _ in EXPECTED_SERVERS}
    # 노드를 맨 앞에 — 서비스가 앉아 있는 바닥이라 먼저 보는 것이 자연스럽다.
    node_keys = sorted(
        k for k in rows if k not in expected_keys and k.startswith(cluster_metrics.NODE_KEY_PREFIX)
    )
    # 기대 목록에도 노드에도 없는 것(옛 VM 잔재 등)은 맨 뒤에(확장성 + 정직)
    other_keys = sorted(
        k
        for k in rows
        if k not in expected_keys and not k.startswith(cluster_metrics.NODE_KEY_PREFIX)
    )
    servers = [_row_out(rows[k], k, rows[k].label) for k in node_keys]
    servers += [_row_out(rows.get(key), key, label) for key, label in EXPECTED_SERVERS]
    servers += [_row_out(rows[k], k, rows[k].label) for k in other_keys]
    # 서버별 추이(그래프용) — 데이터 있는 서버에만
    for s in servers:
        if not s.get("no_data"):
            s["history"] = _history(db, s["server_key"], range_key)

    # LLM 사용량·비용 — 모델별 누적 토큰 × 공시 단가($/1M). 실비용 아닌 운영 참고 추정치.
    models = db.query(AiModelConfig).all()
    by_provider: dict[str, dict] = {}
    tot_in = tot_out = 0
    tot_cost = 0.0
    for m in models:
        cost = (m.tokens_in / 1e6) * m.cost_in_usd + (m.tokens_out / 1e6) * m.cost_out_usd
        tot_in += m.tokens_in
        tot_out += m.tokens_out
        tot_cost += cost
        p = by_provider.setdefault(m.provider, {"provider": m.provider, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0})
        p["tokens_in"] += m.tokens_in
        p["tokens_out"] += m.tokens_out
        p["cost_usd"] += cost

    alert_count = sum(len(s.get("alerts") or []) for s in servers)
    return {
        "servers": servers,
        "alert_count": alert_count,
        "thresholds": CRIT,
        "llm": {
            "tokens_in": tot_in,
            "tokens_out": tot_out,
            "est_cost_usd": round(tot_cost, 4),
            "providers": [
                {**p, "cost_usd": round(p["cost_usd"], 4)}
                for p in sorted(by_provider.values(), key=lambda x: -x["cost_usd"])
            ],
        },
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "stale_after_sec": STALE_AFTER_SEC,
    }
