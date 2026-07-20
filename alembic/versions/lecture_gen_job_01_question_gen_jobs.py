"""AI 확인문항 생성 잡 — lecture_question_gen_jobs (비동기 생성 상태)

Revision ID: lecture_gen_job_01
Revises: course_exam_img_01
Create Date: 2026-07-20

AI 문항 생성을 동기 대기에서 백그라운드로 전환하기 위한 잡 상태 테이블. 엔드포인트는 잡을
만들고 즉시 반환, 러너가 STT+생성을 수행하며 상태(pending→running→done|error)와 요약을
갱신한다. 프론트는 이 행을 폴링한다. 멱등: 테이블 존재 검사 후 건너뛴다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "lecture_gen_job_01"
down_revision: Union[str, None] = "course_exam_img_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "lecture_question_gen_jobs" in insp.get_table_names():
        return
    op.create_table(
        "lecture_question_gen_jobs",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("lecture_id", sa.CHAR(36), nullable=False),
        sa.Column("requested_by", sa.CHAR(36), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("created_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("transcript_used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("transcript_source", sa.String(20), nullable=True),
        sa.Column("self_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("captcha_candidates", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bank_candidates", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("discard_candidates", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verify_error", sa.String(255), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    # lecture_id·requested_by는 소프트 참조(FK 없음) — 이 코드베이스 관례(LectureTranscript 등).
    op.create_index("ix_lqgj_lecture", "lecture_question_gen_jobs", ["lecture_id"])
    op.create_index("ix_lqgj_requested_by", "lecture_question_gen_jobs", ["requested_by"])


def downgrade() -> None:
    op.drop_table("lecture_question_gen_jobs")
