"""생성 잡 세부 단계 — lecture_question_gen_jobs.phase (자막 변환/문항 생성/검증)

Revision ID: lecture_gen_phase_01
Revises: lecture_gen_job_01
Create Date: 2026-07-20

running 중 '지금 무슨 단계인지'(transcribing|generating|verifying)를 강사에게 보여주기 위한
컬럼. 실시간 %가 아니라 거친 단계 표시. 멱등: 컬럼 존재 검사 후 건너뛴다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "lecture_gen_phase_01"
down_revision: Union[str, None] = "lecture_gen_job_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "lecture_question_gen_jobs" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("lecture_question_gen_jobs")}
    if "phase" not in cols:
        op.add_column("lecture_question_gen_jobs", sa.Column("phase", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("lecture_question_gen_jobs", "phase")
