"""카카오페이·토스페이먼츠 운영 결제 필드

Revision ID: course_payment_pg_02
Revises: course_order_01
Create Date: 2026-07-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "course_payment_pg_02"
down_revision: Union[str, None] = "course_order_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "courses" in tables:
        columns = _column_names("courses")
        if "price" not in columns:
            op.add_column(
                "courses",
                sa.Column("price", sa.Integer(), nullable=False, server_default="0"),
            )
        if "sale_price" not in columns:
            op.add_column("courses", sa.Column("sale_price", sa.Integer(), nullable=True))
        if "sale_ends_at" not in columns:
            op.add_column("courses", sa.Column("sale_ends_at", sa.DateTime(), nullable=True))

    if "course_orders" in tables:
        columns = _column_names("course_orders")
        if "callback_token_hash" not in columns:
            op.add_column(
                "course_orders", sa.Column("callback_token_hash", sa.String(64), nullable=True)
            )
        if "provider_session" not in columns:
            op.add_column("course_orders", sa.Column("provider_session", sa.JSON(), nullable=True))
        if "receipt_url" not in columns:
            op.add_column(
                "course_orders", sa.Column("receipt_url", sa.String(500), nullable=True)
            )
        if "cancelled_at" not in columns:
            op.add_column(
                "course_orders", sa.Column("cancelled_at", sa.DateTime(), nullable=True)
            )
        if "cancel_reason" not in columns:
            op.add_column(
                "course_orders", sa.Column("cancel_reason", sa.String(200), nullable=True)
            )
        index_names = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("course_orders")}
        if "ix_order_provider_payment_key" not in index_names:
            op.create_index(
                "ix_order_provider_payment_key",
                "course_orders",
                ["provider", "payment_key"],
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "course_orders" in tables:
        index_names = {i["name"] for i in inspector.get_indexes("course_orders")}
        if "ix_order_provider_payment_key" in index_names:
            op.drop_index("ix_order_provider_payment_key", table_name="course_orders")
        columns = _column_names("course_orders")
        for name in (
            "cancel_reason",
            "cancelled_at",
            "receipt_url",
            "provider_session",
            "callback_token_hash",
        ):
            if name in columns:
                op.drop_column("course_orders", name)

    if "courses" in tables:
        columns = _column_names("courses")
        for name in ("sale_ends_at", "sale_price", "price"):
            if name in columns:
                op.drop_column("courses", name)
