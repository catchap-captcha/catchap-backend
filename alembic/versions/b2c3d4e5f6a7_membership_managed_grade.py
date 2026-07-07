"""membership managed_grade (학년부장 학년 범위)

학년부장(grade_head)이 담당하는 학년을 저장. 비파괴적(컬럼 추가, 기본 NULL).
teacher/org_admin 은 NULL, role=grade_head 일 때만 값이 채워진다.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_col(bind, table: str, col: str) -> bool:
    return any(c["name"] == col for c in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_col(bind, "memberships", "managed_grade"):
        op.add_column(
            "memberships",
            sa.Column("managed_grade", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_col(bind, "memberships", "managed_grade"):
        op.drop_column("memberships", "managed_grade")
