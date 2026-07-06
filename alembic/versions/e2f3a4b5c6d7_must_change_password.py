"""student must_change_password flag

교사/기관 비번 초기화 후 첫 로그인 시 비번 변경 강제용 플래그.
비파괴적(컬럼 추가, 기본 False).

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-07-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_col(bind, table: str, col: str) -> bool:
    return any(c["name"] == col for c in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_col(bind, "student_profiles", "must_change_password"):
        op.add_column(
            "student_profiles",
            sa.Column(
                "must_change_password",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_col(bind, "student_profiles", "must_change_password"):
        op.drop_column("student_profiles", "must_change_password")
