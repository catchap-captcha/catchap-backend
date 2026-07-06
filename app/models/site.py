from datetime import datetime

from sqlalchemy import CHAR, JSON, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class Site(Base, UUIDPk, Timestamps):
    """CAPTCHA API 연동 사이트 (기관 대시보드 'API·사이트 상태' 위젯의 데이터)"""

    __tablename__ = "sites"

    organization_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("organizations.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(150))
    domain: Mapped[str] = mapped_column(String(255))
    allowed_origins: Mapped[dict] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="active")


class ApiKey(Base, UUIDPk, Timestamps):
    """site_key는 공개, secret_key는 발급 시 1회만 노출 — hash만 저장."""

    __tablename__ = "api_keys"

    organization_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    site_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("sites.id"), index=True)
    site_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    secret_key_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|disabled
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ApiUsageLog(Base, UUIDPk, Timestamps):
    __tablename__ = "api_usage_logs"
    # 기관 API 사용량 기간 집계 가속용 (migration ce50a1b2c3d4)
    __table_args__ = (Index("ix_aul_org_created", "organization_id", "created_at"),)

    organization_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    site_id: Mapped[str | None] = mapped_column(CHAR(36), nullable=True, index=True)
    endpoint: Mapped[str] = mapped_column(String(150))
    method: Mapped[str] = mapped_column(String(10))
    status_code: Mapped[int] = mapped_column(default=200)
    latency_ms: Mapped[int] = mapped_column(default=0)


class CaptchaSetting(Base, UUIDPk, Timestamps):
    """캡차설정 화면: 종류 on/off + 라운드당 개수 + 순서 셔플"""

    __tablename__ = "captcha_settings"

    organization_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("organizations.id"), unique=True, index=True
    )
    active_types: Mapped[dict] = mapped_column(
        JSON, default=dict
    )  # {image_select, word_select, drag, arithmetic}
    round_count: Mapped[int] = mapped_column(default=2)
    shuffle: Mapped[bool] = mapped_column(default=True)
