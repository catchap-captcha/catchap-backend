"""chapter_progress — 전체학습 주간 챕터의 단계 진행(이어하기 커서)

Revision ID: e6f7a8b9c0d1
Revises: d1a2b3c4e5f6
Create Date: 2026-07-09
"""
from alembic import op
import sqlalchemy as sa

revision = "e6f7a8b9c0d1"
down_revision = "d1a2b3c4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chapter_progress",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("student_id", sa.CHAR(length=36), nullable=False),
        sa.Column("subject", sa.String(length=20), nullable=False),
        sa.Column("chapter_no", sa.Integer(), nullable=False),
        sa.Column("stages_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["student_id"], ["student_profiles.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "student_id", "subject", "chapter_no", name="uq_chapter_progress"
        ),
    )
    op.create_index(
        "ix_chapter_progress_student_id", "chapter_progress", ["student_id"]
    )
    op.create_index("ix_chapter_progress_subject", "chapter_progress", ["subject"])


def downgrade() -> None:
    op.drop_index("ix_chapter_progress_subject", table_name="chapter_progress")
    op.drop_index("ix_chapter_progress_student_id", table_name="chapter_progress")
    op.drop_table("chapter_progress")
