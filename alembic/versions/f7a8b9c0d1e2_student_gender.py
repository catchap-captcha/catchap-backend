"""student_profiles.gender — 성별(학습분석·외부 익명 집계 인구통계 축)

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-07-09
"""
from alembic import op
import sqlalchemy as sa

revision = "f7a8b9c0d1e2"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("student_profiles", sa.Column("gender", sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column("student_profiles", "gender")
