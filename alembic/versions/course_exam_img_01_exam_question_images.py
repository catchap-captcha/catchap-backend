"""코스 시험 문항 이미지 — course_exam_questions.images (JSON)

Revision ID: course_exam_img_01
Revises: lecture_transcript_01
Create Date: 2026-07-20

강의 문항처럼 시험 문항에도 이미지(프롬프트·보기)를 지원한다. 강의 문항 payload의
prompt_image/option_images와 같은 참조 구조({id, ext})를 시험 문항 전용 JSON 컬럼에 담는다.
파일은 강의 문항과 같은 media/questions/ 디렉터리를 공유(UUID 키라 충돌 없음). 멱등: 컬럼
존재 검사 후 건너뛴다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "course_exam_img_01"
down_revision: Union[str, None] = "lecture_transcript_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "course_exam_questions" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("course_exam_questions")}
    if "images" not in cols:
        op.add_column("course_exam_questions", sa.Column("images", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("course_exam_questions", "images")
