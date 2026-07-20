"""확인문항 채점에 question_id — lecture_checkpoint_events.question_id (문항별 난이도·불량 탐지)

Revision ID: lecture_cp_qid_01
Revises: lecture_gen_phase_01
Create Date: 2026-07-20

강의 확인문항 채점 이벤트에 어느 문항이었는지 남겨, '특정 문항이 유독 어렵거나 잘못
만들어졌는지'를 문항별로 볼 수 있게 한다(강사 홈). 소프트 참조(FK 없음). 멱등: 컬럼 검사.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "lecture_cp_qid_01"
down_revision: Union[str, None] = "lecture_gen_phase_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "lecture_checkpoint_events" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("lecture_checkpoint_events")}
    if "question_id" not in cols:
        op.add_column("lecture_checkpoint_events", sa.Column("question_id", sa.CHAR(36), nullable=True))
        op.create_index("ix_lce_question", "lecture_checkpoint_events", ["question_id"])


def downgrade() -> None:
    op.drop_index("ix_lce_question", table_name="lecture_checkpoint_events")
    op.drop_column("lecture_checkpoint_events", "question_id")
