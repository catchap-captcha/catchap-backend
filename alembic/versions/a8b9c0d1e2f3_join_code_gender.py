"""student_join_codes.gender — 선생님이 코드 생성 시 입력하는 성별(활성화 시 학생에 복사)

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-07-09
"""
from alembic import op
import sqlalchemy as sa

revision = "a8b9c0d1e2f3"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("student_join_codes", sa.Column("gender", sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column("student_join_codes", "gender")
