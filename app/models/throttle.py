from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class LoginThrottle(Base, UUIDPk, Timestamps):
    """로그인 실패 카운터 — 5회 이상 실패 시 캡차 요구, 성공 시 리셋.

    identifier: "user:<email>" | "student:<login_id>"
    """

    __tablename__ = "login_throttle"

    identifier: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    fail_count: Mapped[int] = mapped_column(default=0)
