"""문제은행 SRS — student_question_states 테이블

Revision ID: bank_srs_01
Revises: course_tbl_01
Create Date: 2026-07-19

문제은행 규모 확장 1단계(설계: docs/question-bank-scale-design.md). 학생×문항 SRS
상태의 정본 테이블 — 행이 없으면 '안 푼(new)', mastered=연속 2회 정답, 만기
(next_review_at)에만 복습 재등장. LearningAttempt가 원장이고 이 테이블은 파생
상태라 유실 시 백필(manage_bank_srs.py)로 재구축한다.

FK 없음(소프트 참조) — 라이브 덤프로 재생성한 DB의 collation 불일치 선례
(course_tbl_01)를 따른다. 멱등: 존재 검사 후 건너뛴다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "bank_srs_01"
down_revision: Union[str, None] = "course_tbl_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if "student_question_states" not in insp.get_table_names():
        op.create_table(
            "student_question_states",
            sa.Column("id", sa.CHAR(36), primary_key=True),
            sa.Column("student_id", sa.CHAR(36), nullable=False),
            sa.Column("question_id", sa.String(80), nullable=False),
            sa.Column("subject", sa.String(20), nullable=False),
            sa.Column("state", sa.String(10), nullable=False, server_default="learning"),
            sa.Column("correct_streak", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("wrong_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_result", sa.String(10), nullable=False),
            sa.Column("next_review_at", sa.DateTime(), nullable=True),
            sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_student_question_states_student_id", "student_question_states", ["student_id"])
        op.create_index("ix_sqs_student_subject", "student_question_states", ["student_id", "subject"])
        op.create_index(
            "ix_student_question_states_next_review_at", "student_question_states", ["next_review_at"]
        )
        op.create_unique_constraint(
            "uq_sqs_student_question", "student_question_states", ["student_id", "question_id"]
        )


def downgrade() -> None:
    op.drop_table("student_question_states")
