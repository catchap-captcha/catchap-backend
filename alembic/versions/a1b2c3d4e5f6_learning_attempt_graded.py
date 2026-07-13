"""learning_attempts.graded — 서버 채점 여부(무채점 자기신고 위조 차단, 적대적검토 #4/#5)

Revision ID: a1b2c3d4e5f6
Revises: f6a7b8c9d1e2
Create Date: 2026-07-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f6a7b8c9d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("learning_attempts")}
    if "graded" not in cols:
        # 기존 행은 전부 서버 채점 경로(위젯/game-answer)로 쌓였으므로 True로 백필한다.
        op.add_column(
            "learning_attempts",
            sa.Column("graded", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        )
        op.create_index("ix_la_graded", "learning_attempts", ["graded"])


def downgrade() -> None:
    op.drop_index("ix_la_graded", table_name="learning_attempts")
    op.drop_column("learning_attempts", "graded")
