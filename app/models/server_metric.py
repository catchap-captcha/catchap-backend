"""서버 자원 모니터링 스냅샷 — 각 VM(백엔드·DB·GPU STT·프론트)의 최신 지표 1행.

왜 '최신 1행'인가(시계열 아님): 이 화면의 1차 목적은 '지금 각 서버가 건강한가'(현재 상태
대시보드)다. 시계열/추이 그래프는 데이터·차트 비용이 크므로 v2로 미루고, 우선 서버별
최신 스냅샷을 upsert(server_key 유니크)해 '현황판'을 만든다.

수집 경로(2026-07-21):
- 백엔드 자신은 요청 시 psutil로 자기 호스트를 즉시 측정해 이 테이블에 upsert(에이전트 불요).
- 다른 서버(DB·GPU·프론트)는 각 VM에서 `scripts/metrics_agent.py`(psutil+nvidia-smi)가
  주기적으로 POST /internal/metrics 로 밀어넣는다(배포 시 연결 — 지금은 로컬 시드로 데모).
공부 키워드: node metrics, psutil, nvidia-smi, push-based metrics ingest.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class ServerMetric(Base, UUIDPk, Timestamps):
    __tablename__ = "server_metrics"

    # 서버 식별 키(유니크) — backend|db|gpu-stt|frontend 등. 같은 키로 오면 최신값으로 덮어쓴다.
    server_key: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(60))  # 표시 이름(예: 백엔드 API)
    host: Mapped[str | None] = mapped_column(String(80), nullable=True)  # 내부 IP/호스트명(선택)

    cpu_pct: Mapped[float] = mapped_column(Float, default=0.0)
    cpu_cores: Mapped[int] = mapped_column(Integer, default=0)
    load1: Mapped[float | None] = mapped_column(Float, nullable=True)  # 1분 load avg(윈도우는 None)

    mem_pct: Mapped[float] = mapped_column(Float, default=0.0)
    mem_used_mb: Mapped[int] = mapped_column(Integer, default=0)
    mem_total_mb: Mapped[int] = mapped_column(Integer, default=0)

    disk_pct: Mapped[float] = mapped_column(Float, default=0.0)
    disk_used_gb: Mapped[float] = mapped_column(Float, default=0.0)
    disk_total_gb: Mapped[float] = mapped_column(Float, default=0.0)

    # GPU — GPU 없는 서버는 gpu_present=False(나머지 GPU 필드 None)
    gpu_present: Mapped[bool] = mapped_column(Boolean, default=False)
    gpu_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    gpu_util_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    gpu_mem_used_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gpu_mem_total_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 에이전트가 '측정한' 시각(수신 시각 아님) — 대시보드가 신선도(오래됨) 판정에 쓴다.
    collected_at: Mapped[datetime] = mapped_column(DateTime)
