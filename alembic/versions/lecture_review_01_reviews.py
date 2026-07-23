"""강의 수강 후기 — lecture_reviews

Revision ID: lecture_review_01
Revises: lecture_deleted_at_01
Create Date: 2026-07-23

플레이어 '수강 후기' 탭을 실동작으로. 별점(1~5)+선택 텍스트, (student_id, lecture_id) 유니크로
학생당 강의당 1행 upsert. 수강생만 작성(엔드포인트에서 _require_enrolled). 삭제는 status='deleted'.
멱등: 테이블 존재 검사(재실행 안전).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "lecture_review_01"
down_revision: Union[str, None] = "lecture_deleted_at_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "lecture_reviews" in insp.get_table_names():
        return
    op.create_table(
        "lecture_reviews",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("student_id", sa.CHAR(36), nullable=False),
        sa.Column("lecture_id", sa.CHAR(36), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_review_student", "lecture_reviews", ["student_id"])
    op.create_index("ix_review_lecture_status", "lecture_reviews", ["lecture_id", "status"])
    op.create_index(
        "uq_lecture_review", "lecture_reviews", ["student_id", "lecture_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_lecture_review", table_name="lecture_reviews")
    op.drop_index("ix_review_lecture_status", table_name="lecture_reviews")
    op.drop_index("ix_review_student", table_name="lecture_reviews")
    op.drop_table("lecture_reviews")
