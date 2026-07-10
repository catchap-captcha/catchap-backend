from datetime import datetime

from sqlalchemy import CHAR, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class User(Base, UUIDPk, Timestamps):
    """이메일 계정 사용자 (학부모/교사/기관 관리자/운영자). 학생은 student_profiles."""

    __tablename__ = "users"

    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    role: Mapped[str] = mapped_column(
        String(20), index=True
    )  # parent | teacher | org_admin | ops
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|pending|disabled
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    two_factor_enabled: Mapped[bool] = mapped_column(default=False)
    # 임시 비번(기관 승인 발급/관리자 초기화)으로 로그인 시 True → 첫 로그인에서 강제 변경
    must_change_password: Mapped[bool] = mapped_column(default=False)
    organization_id: Mapped[str | None] = mapped_column(
        CHAR(36), index=True, nullable=True
    )  # 주 소속 기관 (멀티 소속은 memberships)
