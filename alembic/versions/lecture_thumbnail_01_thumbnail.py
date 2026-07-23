"""강의 썸네일 — lectures.thumbnail_ext (목록·카드 대표 이미지 확장자)

Revision ID: lecture_thumbnail_01
Revises: lecture_review_01
Create Date: 2026-07-24

강의별 썸네일 이미지를 업로드·서빙하기 위한 확장자 컬럼. 경로는 영상과 동일하게 저장하지
않고 LECTURE_MEDIA_DIR/thumbnails/{id}{thumbnail_ext}로 유도한다(경로조작 원천 차단).
없으면 NULL(썸네일 미설정). 멱등: 컬럼 존재 검사(재실행 안전).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "lecture_thumbnail_01"
down_revision: Union[str, None] = "lecture_review_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("lectures")}
    if "thumbnail_ext" not in cols:
        op.add_column("lectures", sa.Column("thumbnail_ext", sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column("lectures", "thumbnail_ext")
