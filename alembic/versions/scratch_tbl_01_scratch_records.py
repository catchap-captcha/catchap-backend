"""scratch_records — 연습장 필기 원본(학습 인사이트, B 백엔드)

Revision ID: scratch_tbl_01
Revises: consent_tbl_01
Create Date: 2026-07-14

아동 필적이라 민감 개인정보: strokes(원본)는 보존 동의 없으면 탈퇴/보존기한 시 파기,
집계 지표는 익명 보존. content_id·subject로 과목별·문항별 조회.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "scratch_tbl_01"
down_revision: Union[str, None] = "consent_tbl_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("scratch_records"):
        return
    op.create_table(
        "scratch_records",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("student_id", sa.CHAR(36), sa.ForeignKey("student_profiles.id"), nullable=False),
        sa.Column("organization_id", sa.CHAR(36), nullable=True),
        sa.Column("subject", sa.String(20), nullable=False),
        sa.Column("content_id", sa.String(80), nullable=True),
        sa.Column("strokes", sa.JSON(), nullable=True),
        sa.Column("stroke_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("distance_px", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_write_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("draw_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("purged", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("consent_retain", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_scratch_student", "scratch_records", ["student_id"])
    op.create_index("ix_scratch_org", "scratch_records", ["organization_id"])
    op.create_index("ix_scratch_subject", "scratch_records", ["subject"])
    op.create_index("ix_scratch_content", "scratch_records", ["content_id"])


def downgrade() -> None:
    op.drop_table("scratch_records")
