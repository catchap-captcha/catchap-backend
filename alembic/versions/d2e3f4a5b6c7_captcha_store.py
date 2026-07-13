"""captcha_store — 메인 캡차 challenge/token 워커 간 공유 저장

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-07-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("captcha_store"):
        return
    op.create_table(
        "captcha_store",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("k", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("used", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("k", name="uq_captcha_k"),
    )
    op.create_index("ix_captcha_kind", "captcha_store", ["kind"])
    op.create_index("ix_captcha_exp", "captcha_store", ["expires_at"])


def downgrade() -> None:
    op.drop_table("captcha_store")
