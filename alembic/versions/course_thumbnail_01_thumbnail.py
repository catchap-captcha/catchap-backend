"""코스 썸네일 — courses.thumbnail_ext (강의 없이도 코스 자체 대표 이미지)

Revision ID: course_thumbnail_01
Revises: lecture_report_01
Create Date: 2026-08-06

코스 대표 이미지를 '강의 유래 유도'에만 의존하지 않고 코스 자체에 달 수 있게 하는 확장자
컬럼. 강의가 없는 코스도 커버를 가질 수 있다. 경로는 강의 썸네일과 동일 원칙으로
lectures/course-thumbnails/{id}{thumbnail_ext}로 유도(경로조작 원천 차단). 없으면 NULL.
멱등: 컬럼 존재 검사(재실행 안전).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "course_thumbnail_01"
down_revision: Union[str, None] = "lecture_report_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("courses")}
    if "thumbnail_ext" not in cols:
        op.add_column("courses", sa.Column("thumbnail_ext", sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column("courses", "thumbnail_ext")
