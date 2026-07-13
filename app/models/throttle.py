from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class LoginThrottle(Base, UUIDPk, Timestamps):
    """로그인 실패 카운터 — 5회 이상 실패 시 캡차 요구, 성공 시 리셋.

    identifier: "user:<email>" | "student:<login_id>"
    """

    __tablename__ = "login_throttle"

    identifier: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    fail_count: Mapped[int] = mapped_column(default=0)


class CaptchaStore(Base, UUIDPk, Timestamps):
    """메인 캡차(forest) challenge·token 공유 저장 — 워커 간 공유용(InMemory 대체).

    uvicorn 멀티워커에서 InMemory store는 challenge를 발급한 워커와 verify를 받는
    워커가 달라 '정답이어도 실패'가 났다(약 50%). DB에 두면 워커 무관 공유된다.
    k=challenge_id 또는 token, kind=challenge|token, payload=JSON 직렬화.
    """

    __tablename__ = "captcha_store"

    k: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # challenge | token
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    used: Mapped[bool] = mapped_column(Boolean, default=False)  # token 단일사용
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
