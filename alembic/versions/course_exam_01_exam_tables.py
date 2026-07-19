"""코스 수료 시험 — 4테이블(문항·회차·응답·수료)

Revision ID: course_exam_01
Revises: orgless_learn_01
Create Date: 2026-07-19

설계: docs/course-exam-design.md (완전학습 mastery — 틀린 것만 재출제, 누적 전 문항
정답=수료). 정복 집합은 course_exam_attempts에서 파생(별도 상태 테이블 없음).
FK 없음(소프트 참조) — 라이브 덤프 재생성 DB collation 불일치 선례(course_tbl_01).
멱등: 존재 검사 후 건너뛴다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "course_exam_01"
down_revision: Union[str, None] = "orgless_learn_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = insp.get_table_names()

    if "course_exam_questions" not in tables:
        op.create_table(
            "course_exam_questions",
            sa.Column("id", sa.CHAR(36), primary_key=True),
            sa.Column("course_id", sa.CHAR(36), nullable=False),
            sa.Column("prompt", sa.Text(), nullable=False),
            sa.Column("options", sa.JSON(), nullable=False),
            sa.Column("answer_indexes", sa.JSON(), nullable=False),
            sa.Column("explain", sa.Text(), nullable=True),
            sa.Column("origin", sa.String(20), nullable=False, server_default="manual"),
            sa.Column("source", sa.String(300), nullable=True),
            sa.Column("origin_lecture_question_id", sa.CHAR(36), nullable=True),
            sa.Column("order_no", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(10), nullable=False, server_default="draft"),
            sa.Column("created_by", sa.CHAR(36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_ceq_course_id", "course_exam_questions", ["course_id"])
        op.create_index("ix_ceq_course_status", "course_exam_questions", ["course_id", "status"])

    if "course_exam_sittings" not in tables:
        op.create_table(
            "course_exam_sittings",
            sa.Column("id", sa.CHAR(36), primary_key=True),
            sa.Column("course_id", sa.CHAR(36), nullable=False),
            sa.Column("student_id", sa.CHAR(36), nullable=False),
            sa.Column("questions", sa.JSON(), nullable=False),
            sa.Column("submitted_at", sa.DateTime(), nullable=True),
            sa.Column("total", sa.Integer(), nullable=True),
            sa.Column("correct", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_ces_course_id", "course_exam_sittings", ["course_id"])
        op.create_index("ix_ces_student_id", "course_exam_sittings", ["student_id"])
        op.create_index("ix_ces_student_course", "course_exam_sittings", ["student_id", "course_id"])

    if "course_exam_attempts" not in tables:
        op.create_table(
            "course_exam_attempts",
            sa.Column("id", sa.CHAR(36), primary_key=True),
            sa.Column("student_id", sa.CHAR(36), nullable=False),
            sa.Column("course_id", sa.CHAR(36), nullable=False),
            sa.Column("question_id", sa.CHAR(36), nullable=False),
            sa.Column("sitting_id", sa.CHAR(36), nullable=False),
            sa.Column("result", sa.String(10), nullable=False),
            sa.Column("answer", sa.JSON(), nullable=True),
            sa.Column("solve_time_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        for col in ("student_id", "course_id", "question_id", "sitting_id"):
            op.create_index(f"ix_cea_{col}", "course_exam_attempts", [col])
        op.create_index(
            "ix_cea_student_course_q", "course_exam_attempts",
            ["student_id", "course_id", "question_id"],
        )

    if "course_completions" not in tables:
        op.create_table(
            "course_completions",
            sa.Column("id", sa.CHAR(36), primary_key=True),
            sa.Column("student_id", sa.CHAR(36), nullable=False),
            sa.Column("course_id", sa.CHAR(36), nullable=False),
            sa.Column("passed_at", sa.DateTime(), nullable=False),
            sa.Column("question_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("sittings_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("perfect", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("student_id", "course_id", name="uq_completion_student_course"),
        )
        op.create_index("ix_cc_student_id", "course_completions", ["student_id"])
        op.create_index("ix_cc_course_id", "course_completions", ["course_id"])


def downgrade() -> None:
    for t in ("course_completions", "course_exam_attempts", "course_exam_sittings", "course_exam_questions"):
        op.drop_table(t)
