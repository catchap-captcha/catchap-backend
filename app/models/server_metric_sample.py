"""서버 지표 시계열 표본 — 추이 그래프(append-only). ServerMetric(최신 1행)과 짝.

왜 별 테이블인가: server_metrics는 '지금 값'(upsert 1행)이라 추이를 못 그린다. 여기 표본을
수집 때마다 append 하고, 대시보드가 서버별 최근 구간을 라인 차트로 보여준다. 무한 증가를
막으려고 보존창(retention) 밖 오래된 표본은 인제스트 때 함께 정리한다(현황+단기 추이가
목적이라 장기 보관은 불필요 — 필요하면 별도 롱텀 스토리지로).
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class ServerMetricSample(Base, UUIDPk, Timestamps):
    __tablename__ = "server_metric_samples"

    server_key: Mapped[str] = mapped_column(String(40), index=True)
    cpu_pct: Mapped[float] = mapped_column(Float, default=0.0)
    mem_pct: Mapped[float] = mapped_column(Float, default=0.0)
    gpu_util_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime, index=True)
