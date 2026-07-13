"""learning_attempts.content_id CHAR(36) → VARCHAR(80) — 뱅크 슬러그 id(최장 49자) 수용

Revision ID: a7b8c9d0e1f2
Revises: f5a6b7c8d9e0
Create Date: 2026-07-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f5a6b7c8d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "learning_attempts", "content_id",
        existing_type=sa.CHAR(36), type_=sa.String(80), existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "learning_attempts", "content_id",
        existing_type=sa.String(80), type_=sa.CHAR(36), existing_nullable=True,
    )
