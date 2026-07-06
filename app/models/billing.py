from sqlalchemy import CHAR, JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class Plan(Base, UUIDPk, Timestamps):
    """요금제 (Basic/Pro/Enterprise) — 조회 전용, 결제 실행은 mock"""

    __tablename__ = "plans"

    key: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(60))
    monthly_price: Mapped[int] = mapped_column(default=0)
    yearly_price: Mapped[int] = mapped_column(default=0)
    api_quota: Mapped[int] = mapped_column(default=0)
    student_seats: Mapped[int] = mapped_column(default=0)
    teacher_seats: Mapped[int] = mapped_column(default=0)
    features: Mapped[dict] = mapped_column(JSON, default=list)
    order_no: Mapped[int] = mapped_column(default=0)


class Subscription(Base, UUIDPk, Timestamps):
    __tablename__ = "subscriptions"

    organization_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("organizations.id"), unique=True, index=True
    )
    plan_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("plans.id"))
    billing_cycle: Mapped[str] = mapped_column(String(10), default="monthly")  # monthly|yearly
    status: Mapped[str] = mapped_column(String(20), default="active")
    auto_renew: Mapped[bool] = mapped_column(default=True)


class PaymentMethod(Base, UUIDPk, Timestamps):
    __tablename__ = "payment_methods"

    organization_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    card_brand: Mapped[str] = mapped_column(String(30))
    card_last4: Mapped[str] = mapped_column(String(4))
    is_default: Mapped[bool] = mapped_column(default=False)


class Invoice(Base, UUIDPk, Timestamps):
    __tablename__ = "invoices"

    organization_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    invoice_no: Mapped[str] = mapped_column(String(30), unique=True)
    description: Mapped[str] = mapped_column(String(150))
    amount: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(20), default="paid")
    billed_on: Mapped[str | None] = mapped_column(String(20), nullable=True)
