"""운영 모니터링 — 서버별 자원(CPU/메모리/디스크/GPU) + LLM API 사용량·비용.

두 경로:
- POST /internal/metrics : 각 VM의 에이전트(scripts/metrics_agent.py)가 X-Metrics-Token으로
  자기 지표를 밀어넣는다(server_key 유니크 upsert). 배포 시 연결.
- GET /ops/monitoring : 운영자 대시보드. 요청 시 백엔드 자신을 psutil로 즉시 측정(에이전트
  불요)해 upsert하고, 모든 서버 최신값 + 신선도(오래됨) + LLM 사용량 집계를 돌려준다.

설계 주석(팀 학습용): push-based node metrics. 최신 1행/서버(현황판) — 시계열/추이는 v2.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.permissions import Principal, require_ops
from app.db.session import get_db
from app.models import AiModelConfig, ServerMetric, ServerMetricSample
from app.services import host_metrics

router = APIRouter()

RETENTION_HOURS = 6  # 표본 보존창 — 그 밖은 인제스트/self-collect 때 정리(단기 추이가 목적)
HISTORY_POINTS = 60  # 그래프에 내릴 최대 표본 수(초과분은 균등 다운샘플)

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

# 대시보드가 보여줄 '기대 서버' — 데이터가 아직 없어도 카드로 노출(미수집 표시). CatChap 5대 VM.
EXPECTED_SERVERS: list[tuple[str, str]] = [
    ("backend", "백엔드 API"),
    ("db", "DB (MySQL)"),
    ("gpu-stt", "GPU STT 워커"),
    ("frontend", "프론트"),
]
STALE_AFTER_SEC = 120  # 이 시간 넘게 갱신 없으면 '오래됨'(에이전트 중단 의심)


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
    # 추이용 표본 1개 append(가벼운 3지표만)
    db.add(ServerMetricSample(
        server_key=key,
        cpu_pct=float(snap.get("cpu_pct") or 0.0),
        mem_pct=float(snap.get("mem_pct") or 0.0),
        gpu_util_pct=snap.get("gpu_util_pct"),
        collected_at=ts,
    ))
    return row


def _prune(db: Session) -> None:
    """보존창 밖 표본 정리 — 무한 증가 방지(현황+단기 추이만 유지)."""
    cutoff = datetime.now() - timedelta(hours=RETENTION_HOURS)
    db.query(ServerMetricSample).filter(ServerMetricSample.collected_at < cutoff).delete(
        synchronize_session=False
    )


def _history(db: Session, server_key: str) -> dict:
    """서버별 최근 표본을 그래프용 배열로 — HISTORY_POINTS 초과 시 균등 다운샘플."""
    cutoff = datetime.now() - timedelta(hours=RETENTION_HOURS)
    rows = (
        db.query(ServerMetricSample)
        .filter(ServerMetricSample.server_key == server_key, ServerMetricSample.collected_at >= cutoff)
        .order_by(ServerMetricSample.collected_at)
        .all()
    )
    if len(rows) > HISTORY_POINTS:
        step = len(rows) / HISTORY_POINTS
        rows = [rows[int(i * step)] for i in range(HISTORY_POINTS)]
    return {
        "t": [r.collected_at.isoformat(timespec="seconds") for r in rows],
        "cpu": [round(r.cpu_pct, 1) for r in rows],
        "mem": [round(r.mem_pct, 1) for r in rows],
        "gpu": [round(r.gpu_util_pct, 1) if r.gpu_util_pct is not None else None for r in rows],
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
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """운영자 모니터링 대시보드 — 서버별 자원 + LLM 사용량·비용.

    백엔드 자신은 요청 시 psutil로 즉시 측정해 upsert(항상 신선). 다른 서버는 에이전트가
    밀어넣은 최신값(없으면 no_data). LLM은 AiModelConfig 누적 토큰×단가로 추정 비용 집계."""
    # 백엔드 self-collect — 에이전트 없이도 이 서버는 실데이터(+표본 append로 추이가 쌓인다)
    try:
        _upsert(db, host_metrics.collect("backend", "백엔드 API", host="self"))
        _prune(db)
        db.commit()
    except Exception:
        db.rollback()  # 측정 실패해도 대시보드 자체는 나머지로 뜬다(정직: 백엔드가 no_data로 보일 수 있음)

    rows = {r.server_key: r for r in db.query(ServerMetric).all()}
    servers = [_row_out(rows.get(key), key, label) for key, label in EXPECTED_SERVERS]
    # 기대 목록에 없는 추가 서버도 뒤에 붙인다(확장성)
    extra = [k for k in rows if k not in {k for k, _ in EXPECTED_SERVERS}]
    servers += [_row_out(rows[k], k, rows[k].label) for k in extra]
    # 서버별 추이(그래프용) — 데이터 있는 서버에만
    for s in servers:
        if not s.get("no_data"):
            s["history"] = _history(db, s["server_key"])

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
