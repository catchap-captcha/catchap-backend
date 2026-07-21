"""학생 코스 수강신청 — course_enrollments

Revision ID: course_enroll_01
Revises: server_hourly_01
Create Date: 2026-07-22

무료 이수·수료 검증형의 자유 신청·취소(Coursera 무료 모델). (student_id, course_id) 유니크로
학생당 코스당 1행 upsert, 취소는 status='withdrawn'(진행 이력 보존). 멱등: 테이블 존재 검사.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "course_enroll_01"
down_revision: Union[str, None] = "server_hourly_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "course_enrollments" in insp.get_table_names():
        return
    op.create_table(
        "course_enrollments",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("student_id", sa.CHAR(36), nullable=False),
        sa.Column("course_id", sa.CHAR(36), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("enrolled_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_enroll_student", "course_enrollments", ["student_id"])
    op.create_index("ix_enroll_course", "course_enrollments", ["course_id"])
    op.create_index(
        "ix_enroll_student_course", "course_enrollments", ["student_id", "course_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_enroll_student_course", table_name="course_enrollments")
    op.drop_index("ix_enroll_course", table_name="course_enrollments")
    op.drop_index("ix_enroll_student", table_name="course_enrollments")
    op.drop_table("course_enrollments")
