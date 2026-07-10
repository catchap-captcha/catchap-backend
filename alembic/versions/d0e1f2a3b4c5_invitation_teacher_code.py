"""invitation.teacher_code — 교사 초대링크가 선발급 교사코드를 담아 accept 시 프리필하도록

Revision ID: d0e1f2a3b4c5
Revises: c4d5e6f7a8b9
Create Date: 2026-07-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d0e1f2a3b4c5"
# 원본은 c9d0e1f2a3b4를 부모로 만들어졌으나 이 브랜치의 실제 head(c4d5e6f7a8b9) 뒤로
# 재부모화 — 멀티헤드 방지. upgrade에 컬럼 존재 가드가 있어 순서와 무관하게 안전.
down_revision: Union[str, None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_col(bind, table: str, col: str) -> bool:
    return any(c["name"] == col for c in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_col(bind, "invitations", "teacher_code"):
        op.add_column("invitations", sa.Column("teacher_code", sa.String(20), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_col(bind, "invitations", "teacher_code"):
        op.drop_column("invitations", "teacher_code")
