"""student real_name (학교용 실명 — 교사·기관 화면 전용)

학생 닉네임 변경과 무관하게 교사가 실명으로 식별/검색할 수 있도록.
학생·학부모·랭킹 화면에는 노출하지 않는다. 비파괴적(컬럼 추가, 기본 NULL).

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_col(bind, table: str, col: str) -> bool:
    return any(c["name"] == col for c in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_col(bind, "student_profiles", "real_name"):
        op.add_column("student_profiles", sa.Column("real_name", sa.String(100), nullable=True))
    if not _has_col(bind, "student_join_codes", "real_name"):
        op.add_column("student_join_codes", sa.Column("real_name", sa.String(100), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_col(bind, "student_profiles", "real_name"):
        op.drop_column("student_profiles", "real_name")
    if _has_col(bind, "student_join_codes", "real_name"):
        op.drop_column("student_join_codes", "real_name")
