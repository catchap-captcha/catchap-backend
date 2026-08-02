"""학생 확인문항 신고 — lecture_question_reports

Revision ID: lecture_report_01
Revises: student_interests_01
Create Date: 2026-07-21

학생이 강의 체크포인트 문항을 "문제가 이상해요"로 신고하면 그 강의 강사가 검토한다.
신고 대상 문항은 학생 화면에 노출되지 않는 question_id 대신 챌린지 토큰에서 서버가
복원해 채운다. 소프트 참조(FK 없음) — 신규 lecture 모델 규약(collation 정합).

멱등: 테이블이 이미 있으면(운영 DB에 선반영된 경우) 건너뛴다. 그런 환경에서는
`alembic stamp lecture_report_01`로 적용 표시만 하고 이 upgrade는 실행하지 않는다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "lecture_report_01"
down_revision: Union[str, None] = "student_interests_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "lecture_question_reports" in insp.get_table_names():
        return
    op.create_table(
        "lecture_question_reports",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("lecture_id", sa.CHAR(36), nullable=False),
        sa.Column("question_id", sa.CHAR(36), nullable=False),
        sa.Column("student_id", sa.CHAR(36), nullable=False),
        sa.Column("reason", sa.String(30), nullable=False),
        sa.Column("detail", sa.String(500), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("resolved_by", sa.CHAR(36), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        # 같은 학생이 같은 문항을 재신고하지 못하게 DB 레벨에서 차단.
        sa.UniqueConstraint("student_id", "question_id", name="uq_lqr_student_question"),
    )
    # lecture_id·question_id·student_id는 소프트 참조(FK 없음) — 신규 lecture 모델 관례.
    op.create_index("ix_lqr_lecture", "lecture_question_reports", ["lecture_id"])
    op.create_index("ix_lqr_question", "lecture_question_reports", ["question_id"])
    op.create_index("ix_lqr_student", "lecture_question_reports", ["student_id"])


def downgrade() -> None:
    op.drop_table("lecture_question_reports")
