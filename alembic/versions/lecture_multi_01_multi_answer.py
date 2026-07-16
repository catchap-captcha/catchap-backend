"""lecture_questions.answer_indexes — 확인 문항 다답형(중복 선택) 정답 목록

Revision ID: lecture_multi_01
Revises: lecture_pin_01
Create Date: 2026-07-16

- NULL(기본, 기존 행 전부): 단일 정답 [answer_index]로 본다 — 종전 동작 그대로(하위호환).
- 리스트: options 범위 안의 인덱스 목록(중복 없음). 채점은 select_all(집합 정확 일치,
  부분 정답 없음). answer_indexes를 쓸 때도 answer_index에 첫 값을 함께 채워
  구버전 읽기 경로가 깨지지 않는다.
정답은 종전처럼 payload 밖 분리 컬럼 — 목록/상세 응답의 정답 유출을 구조적으로 차단한다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "lecture_multi_01"
down_revision: Union[str, None] = "lecture_pin_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    cols = {c["name"] for c in insp.get_columns("lecture_questions")}
    if "answer_indexes" not in cols:
        op.add_column(
            "lecture_questions",
            sa.Column("answer_indexes", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("lecture_questions", "answer_indexes")
