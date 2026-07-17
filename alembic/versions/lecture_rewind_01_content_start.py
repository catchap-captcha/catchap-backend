"""lecture_questions.content_start_sec — 문항별 되감기 지점(내용 시작 시점)

되감기 폭 상수(REWIND_SEC=30)는 근거가 없고 대목 길이는 문항마다 다르다(수학 풀이
2~3분 vs 영어 단어 10초). 대목의 시작은 문항의 속성이므로 문항별 필드로 둔다 —
강사가 영상을 보며 "이 내용이 시작되는 시점"을 지정하면 오답 상한 도달 시 거기로
되감는다. NULL(미지정)은 기존 폴백(max(0, cp-30)) 그대로라 기존 행 동작 무변경.

Revision ID: lecture_rewind_01
Revises: lecture_pin_03
"""

import sqlalchemy as sa
from alembic import op

revision = "lecture_rewind_01"
down_revision = "lecture_pin_03"
branch_labels = None
depends_on = None


def _cols(table: str) -> set[str]:
    bind = op.get_bind()
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    if "content_start_sec" not in _cols("lecture_questions"):
        op.add_column(
            "lecture_questions",
            sa.Column("content_start_sec", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("lecture_questions", "content_start_sec")
