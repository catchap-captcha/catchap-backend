"""inquiry answer fields

운영자가 문의에 답변을 작성하면 문의자 이메일로 회신되고, 답변 내용을 기록으로 보관.
비파괴적(컬럼 추가, 전부 nullable).

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-07-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, None] = "e2f3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_col(bind, table: str, col: str) -> bool:
    return any(c["name"] == col for c in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_col(bind, "inquiries", "answer"):
        op.add_column("inquiries", sa.Column("answer", sa.Text(), nullable=True))
    if not _has_col(bind, "inquiries", "answered_at"):
        op.add_column("inquiries", sa.Column("answered_at", sa.DateTime(), nullable=True))
    if not _has_col(bind, "inquiries", "answered_by"):
        op.add_column("inquiries", sa.Column("answered_by", sa.CHAR(36), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    for col in ("answered_by", "answered_at", "answer"):
        if _has_col(bind, "inquiries", col):
            op.drop_column("inquiries", col)
