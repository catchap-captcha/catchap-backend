"""questions — 교육형 문제은행(catchap-service/banks/*.json 적재 대상)

Revision ID: e5f6a7b8c9d1
Revises: d2e3f4a5b6c7
Create Date: 2026-07-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d1"
down_revision: Union[str, None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("questions"):
        return
    op.create_table(
        "questions",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("subject", sa.String(20), nullable=False),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("order_no", sa.Integer(), nullable=False),
        sa.Column("playable", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_q_subject", "questions", ["subject"])
    op.create_index("ix_q_type", "questions", ["type"])
    op.create_index("ix_q_order", "questions", ["order_no"])
    op.create_index("ix_q_playable", "questions", ["playable"])


def downgrade() -> None:
    op.drop_table("questions")
