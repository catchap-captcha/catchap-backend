"""memberships.pending_class — 교사 초대 시 지정 반, 가입 시 자동 배정용

Revision ID: d1a2b3c4e5f6
Revises: c9d0e1f2a3b4
Create Date: 2026-07-09
"""
from alembic import op
import sqlalchemy as sa

revision = "d1a2b3c4e5f6"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("memberships", sa.Column("pending_class", sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column("memberships", "pending_class")
