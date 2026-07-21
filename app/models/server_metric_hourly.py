"""서버 지표 시간별 롤업 — 장기 추이(주간·월간) 그래프용 집계 테이블.

왜 별 테이블인가: raw 표본(server_metric_samples)은 30초 간격이라 월간(30일)을 그대로
보관하면 서버당 8만 행이 넘어 비대해진다. 그래서 raw는 단기(48h)만 두고, 여기 '시간 버킷'
1행에 그 시간의 표본을 합계·개수로 누적(running)해 둔다. 조회 때 avg = sum/count 로 평균을
낸다. 시간당 1행이라 30일이어도 서버당 720행 수준으로 가볍다.

gpu는 서버마다 없을 수 있어(gpu_samples=0), 별도 개수로 센다(평균 분모가 달라야 정확).
인제스트(_upsert)가 매 표본마다 현재 시간 버킷을 upsert 하고, 보존창(35일) 밖은 정리한다.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class ServerMetricHourly(Base, UUIDPk, Timestamps):
    __tablename__ = "server_metric_hourly"

    server_key: Mapped[str] = mapped_column(String(40), index=True)
    # 시간 버킷(분·초 0으로 내림) — (server_key, hour)가 사실상 유니크(코드 upsert로 보장)
    hour: Mapped[datetime] = mapped_column(DateTime, index=True)
    # cpu/mem 표본 누적(평균 = *_sum / samples)
    samples: Mapped[int] = mapped_column(Integer, default=0)
    cpu_sum: Mapped[float] = mapped_column(Float, default=0.0)
    mem_sum: Mapped[float] = mapped_column(Float, default=0.0)
    # gpu는 없는 서버가 있어 분모를 따로 센다(평균 = gpu_sum / gpu_samples, gpu_samples>0일 때)
    gpu_sum: Mapped[float] = mapped_column(Float, default=0.0)
    gpu_samples: Mapped[int] = mapped_column(Integer, default=0)
