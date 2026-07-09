from datetime import date, datetime

from sqlalchemy import (
    CHAR,
    JSON,
    Date,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class Chapter(Base, UUIDPk, Timestamps):
    """과목별 챕터 정의 (6과목 × 5챕터 — 챕터지도/전체학습/개념설명의 콘텐츠 골격)"""

    __tablename__ = "chapters"

    subject: Mapped[str] = mapped_column(String(20), index=True)  # 국어|영어|수학|과학|사회|생활
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
    # 배지명 유일 — 자동지급(개근왕 등) find-or-create 가 race로 중복 배지를 만들지 않게
    __table_args__ = (UniqueConstraint("name", name="uq_badge_name"),)

    name: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(String(200))
    icon: Mapped[str] = mapped_column(String(60))
    color: Mapped[str] = mapped_column(String(20))
    condition_text: Mapped[str] = mapped_column(String(200))
    order_no: Mapped[int] = mapped_column(default=0)


class StudentBadge(Base, UUIDPk, Timestamps):
    __tablename__ = "student_badges"
    # 학생·배지당 1행 (자동지급 동시요청 이중지급 차단)
    __table_args__ = (
        UniqueConstraint("student_id", "badge_id", name="uq_student_badge"),
    )

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
    # 아이템 중복 보유행 차단 (동시 구매 요청 race)
    __table_args__ = (
        UniqueConstraint("student_id", "item_id", name="uq_student_item"),
    )

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


class DailyReward(Base, UUIDPk, Timestamps):
    """GET 경로에서 하루 1회 지급하는 보상의 멱등 장부.

    (student_id, kind, reward_date) UNIQUE로 INSERT 충돌 시 스킵 → 동시요청 이중지급 차단.
    kind 예: 'rank_bonus'(학년 랭킹 상위 보너스 코인).
    """

    __tablename__ = "daily_rewards"
    __table_args__ = (
        UniqueConstraint("student_id", "kind", "reward_date", name="uq_daily_reward"),
    )

    student_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("student_profiles.id"), index=True
    )
    kind: Mapped[str] = mapped_column(String(30), index=True)
    reward_date: Mapped[date] = mapped_column(Date, index=True)
    amount: Mapped[int] = mapped_column(default=0)
