from datetime import datetime

from sqlalchemy import CHAR, DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.security import new_uuid


class Base(DeclarativeBase):
    pass


class UUIDPk:
    """CHAR(36) UUID primary key (MySQL 우선 구현 — BINARY(16) 전환은 추후 협의)"""

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True, default=new_uuid)


def _now() -> datetime:
    # 앱 전체가 로컬 날짜(date.today())로 "오늘/이번 주"를 계산하므로 created_at도 로컬로 통일.
    # (UTC로 쓰면 자정 경계에서 방금 만든 기록이 '어제'로 밀려 집계에서 누락됨)
    return datetime.now()


class Timestamps:
    # Python-side default(생성 즉시 객체에 값 채움) + DB server_default(원시 SQL insert 대비).
    # server_default만 있으면 방금 만든 객체의 created_at이 refresh 전까지 None → 집계 누락.
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, server_default=func.now(), onupdate=_now
    )
