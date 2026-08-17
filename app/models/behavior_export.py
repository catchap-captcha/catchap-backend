"""비동기 행동데이터 내보내기 작업 상태.

운영자 요청은 HTTP 응답보다 오래 걸릴 수 있으므로 요청·진행·결과·만료를 DB에 남긴다.
필터와 반출 사유는 요청 시점 스냅샷이며, 결과 파일 자체는 비공개 Object Storage에 둔다.
"""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CHAR, DateTime, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class BehaviorExportJob(Base, UUIDPk, Timestamps):
    __tablename__ = "behavior_export_jobs"
    __table_args__ = (
        UniqueConstraint("requested_by", "idempotency_key", name="uq_behavior_export_actor_idem"),
    )

    requested_by: Mapped[str] = mapped_column(CHAR(36), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    phase: Mapped[str | None] = mapped_column(String(24), nullable=True)
    filters_json: Mapped[dict] = mapped_column(JSON, default=dict)
    purpose: Mapped[str] = mapped_column(String(255))
    dua_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    k_dropped: Mapped[int] = mapped_column(Integer, default=0)
    object_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
