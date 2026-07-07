from sqlalchemy import CHAR, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class ClassRoom(Base, UUIDPk, Timestamps):
    __tablename__ = "classes"

    organization_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("organizations.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(50))  # "1-2반"
    grade: Mapped[int | None] = mapped_column(nullable=True)
    age_group: Mapped[str | None] = mapped_column(String(30), nullable=True)
    teacher_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("users.id"), nullable=True, index=True
    )
    # 보조/대체 담임 — 담임 결원 시 이 반을 대신 볼 수 있는 교사(선택)
    assistant_teacher_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("users.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="active")
