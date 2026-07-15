"""lecture_questions.pinned/window_sec — 강사가 지정한 시점·구간에 뜨는 고정 문항

Revision ID: lecture_pin_01
Revises: lecture_mat_01
Create Date: 2026-07-16

- pinned=False(기본, 기존 행 전부): position_sec 이후 무작위로 뽑히는 '풀' 문항 — 종전 동작 그대로.
- pinned=True, window_sec=0: 학생이 position_sec에 닿는 순간 그 문항이 뜬다(무작위 간격보다 우선).
- pinned=True, window_sec>0: [position_sec, position_sec+window_sec] 구간 안에서 서버가 고른
  무작위 시점에 뜬다 — 강사는 '문제 풀이 대목'만 지정하고 정확한 초는 모르게 한다.
두 컬럼 다 server_default="0"이라 기존 행은 전부 풀 문항으로 남는다(하위호환).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "lecture_pin_01"
down_revision: Union[str, None] = "lecture_mat_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    cols = {c["name"] for c in insp.get_columns("lecture_questions")}
    if "pinned" not in cols:
        op.add_column(
            "lecture_questions",
            sa.Column("pinned", sa.Boolean(), nullable=False, server_default="0"),
        )
    if "window_sec" not in cols:
        op.add_column(
            "lecture_questions",
            sa.Column("window_sec", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    op.drop_column("lecture_questions", "window_sec")
    op.drop_column("lecture_questions", "pinned")
