from sqlalchemy import CHAR, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class StatBlob(Base, UUIDPk, Timestamps):
    """대시보드/차트/수치 데이터 저장소 (화면 blob 단위 JSON).

    seed가 디자인 값 그대로 넣어두며, 이 테이블의 payload를 수정하면
    해당 그래프/수치가 API를 통해 즉시(≤5초 캐시) 바뀐다.
    key는 design_data.py의 상수명과 1:1 (예: TEACHER_DASHBOARD, RECORD_ACC_SERIES).
    """

    __tablename__ = "stat_blobs"
    __table_args__ = (UniqueConstraint("organization_id", "key", name="uq_stat_org_key"),)

    organization_id: Mapped[str | None] = mapped_column(CHAR(36), index=True, nullable=True)
    key: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
