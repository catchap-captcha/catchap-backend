"""class assistant_teacher_id (보조/대체 담임)

담임 결원 시 반을 대신 볼 수 있는 보조 교사. 비파괴적(컬럼 추가, 기본 NULL).

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_col(bind, table: str, col: str) -> bool:
    return any(c["name"] == col for c in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_col(bind, "classes", "assistant_teacher_id"):
        op.add_column(
            "classes",
            sa.Column("assistant_teacher_id", sa.CHAR(36), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_col(bind, "classes", "assistant_teacher_id"):
        op.drop_column("classes", "assistant_teacher_id")
