"""wrong_answers.chapter_no — 오답노트 챕터 연결 (약한 챕터 미복습 진단·대시보드)

Revision ID: f6a7b8c9d1e2
Revises: e5f6a7b8c9d1
Create Date: 2026-07-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d1e2"
down_revision: Union[str, None] = "e5f6a7b8c9d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("wrong_answers")}
    if "chapter_no" not in cols:
        op.add_column("wrong_answers", sa.Column("chapter_no", sa.Integer(), nullable=True))
        op.create_index("ix_wrong_chapter", "wrong_answers", ["chapter_no"])


def downgrade() -> None:
    op.drop_index("ix_wrong_chapter", table_name="wrong_answers")
    op.drop_column("wrong_answers", "chapter_no")
