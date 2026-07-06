from datetime import datetime

from sqlalchemy import CHAR, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class EmailVerificationCode(Base, UUIDPk, Timestamps):
    """6자리 이메일 인증 코드 — 원문 저장 금지, code_hash만 저장. 1회 사용."""

    __tablename__ = "email_verification_codes"

    email: Mapped[str] = mapped_column(String(255), index=True)
    user_id: Mapped[str | None] = mapped_column(CHAR(36), index=True, nullable=True)
    purpose: Mapped[str] = mapped_column(String(20), default="signup")  # signup | reset
    code_hash: Mapped[str] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PasswordResetToken(Base, UUIDPk, Timestamps):
    __tablename__ = "password_reset_tokens"

    user_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RefreshToken(Base, UUIDPk, Timestamps):
    """refresh token — token_hash만 저장, revoke 가능."""

    __tablename__ = "refresh_tokens"

    user_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    subject_type: Mapped[str] = mapped_column(String(10), default="user")  # user | student
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
