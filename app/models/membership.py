from datetime import datetime

from sqlalchemy import CHAR, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class Membership(Base, UUIDPk, Timestamps):
    """기관 소속 (교사/기관 관리자). 교사 개별코드(T-xxxx)·담당 정보 포함."""

    __tablename__ = "memberships"

    # 교사 코드(T-xxxx) 선발급 → 가입 시 클레임 구조라 nullable
    user_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("users.id"), index=True, nullable=True
    )
    organization_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("organizations.id"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))  # teacher | grade_head | org_admin
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|pending|disabled
    teacher_code: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    position: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 담임 | 수학 전담 등
    # 학년부장(grade_head)이 담당하는 학년(정수). teacher/org_admin은 NULL.
    # role=grade_head 인데 managed_grade 가 있으면 그 학년 범위만 관리 가능.
    managed_grade: Mapped[int | None] = mapped_column(nullable=True)
    career_years: Mapped[int | None] = mapped_column(nullable=True)
    invited_by: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)
    joined_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Invitation(Base, UUIDPk, Timestamps):
    __tablename__ = "invitations"

    organization_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("organizations.id"), index=True
    )
    email: Mapped[str] = mapped_column(String(255), index=True)
    role: Mapped[str] = mapped_column(String(20))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    invited_by: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
