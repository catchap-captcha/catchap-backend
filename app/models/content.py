from datetime import datetime

from sqlalchemy import CHAR, JSON, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class Chapter(Base, UUIDPk, Timestamps):
    """과목별 챕터 정의 (6과목 × 5챕터 — 챕터지도/전체학습/개념설명의 콘텐츠 골격)"""

    __tablename__ = "chapters"

    subject: Mapped[str] = mapped_column(String(20), index=True)  # 국어|영어|수학|과학|역사|생활
    order_no: Mapped[int] = mapped_column()
    name: Mapped[str] = mapped_column(String(100))
    total_questions: Mapped[int] = mapped_column(default=5)
    concept: Mapped[dict] = mapped_column(JSON, default=dict)  # {summary, points[], example}
    status: Mapped[str] = mapped_column(String(20), default="active")


class Content(Base, UUIDPk, Timestamps):
    """교육 콘텐츠/문제 메타 (검색 인덱스 포함)"""

    __tablename__ = "contents"

    organization_id: Mapped[str | None] = mapped_column(CHAR(36), index=True, nullable=True)
    title: Mapped[str] = mapped_column(String(150))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(30), index=True)
    subject: Mapped[str | None] = mapped_column(String(20), index=True, nullable=True)
    difficulty: Mapped[int] = mapped_column(default=1)
    age_group: Mapped[str] = mapped_column(String(30), default="kindergarten")
    icon: Mapped[str | None] = mapped_column(String(60), nullable=True)
    route_hint: Mapped[str | None] = mapped_column(String(120), nullable=True)  # 검색 결과 이동 경로
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_by: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)


class Badge(Base, UUIDPk, Timestamps):
    __tablename__ = "badges"

    name: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(String(200))
    icon: Mapped[str] = mapped_column(String(60))
    color: Mapped[str] = mapped_column(String(20))
    condition_text: Mapped[str] = mapped_column(String(200))
    order_no: Mapped[int] = mapped_column(default=0)


class StudentBadge(Base, UUIDPk, Timestamps):
    __tablename__ = "student_badges"

    student_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("student_profiles.id"), index=True
    )
    badge_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("badges.id"), index=True)
    earned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    progress: Mapped[float] = mapped_column(Float, default=0)  # 도전중 진행률(0~1)


class ShopItem(Base, UUIDPk, Timestamps):
    """프로필 꾸미기 상점 (모자/배경/스티커)"""

    __tablename__ = "shop_items"

    category: Mapped[str] = mapped_column(String(20), index=True)  # hat|background|sticker
    name: Mapped[str] = mapped_column(String(60))
    icon: Mapped[str] = mapped_column(String(60))
    price: Mapped[int] = mapped_column(default=0)
    order_no: Mapped[int] = mapped_column(default=0)


class StudentItem(Base, UUIDPk, Timestamps):
    __tablename__ = "student_items"

    student_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("student_profiles.id"), index=True
    )
    item_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("shop_items.id"), index=True)


class CoinTransaction(Base, UUIDPk, Timestamps):
    __tablename__ = "coin_transactions"

    student_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("student_profiles.id"), index=True
    )
    amount: Mapped[int] = mapped_column()  # +적립 / -사용
    reason: Mapped[str] = mapped_column(String(100))
