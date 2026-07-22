"""강의 휴지통 — lectures.deleted_at (소프트삭제 시각·30일 자동 완전삭제 기준)

Revision ID: lecture_deleted_at_01
Revises: course_enroll_01
Create Date: 2026-07-22

강의 삭제를 '휴지통(복구 가능)'으로 바꾸면서, 언제 휴지통에 들어갔는지를 deleted_at에
기록한다. 30일 지난 휴지통 강의를 자동 완전삭제(파일·문항·전사까지)하는 기준 시각.
멱등: 컬럼 존재 검사. (인덱스는 status와 함께 만료분 조회용.)
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "lecture_deleted_at_01"
down_revision: Union[str, None] = "course_enroll_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("lectures")}
    if "deleted_at" not in cols:
        op.add_column("lectures", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    idx = {i["name"] for i in insp.get_indexes("lectures")}
    if "ix_lecture_status_deleted_at" not in idx:
        op.create_index(
            "ix_lecture_status_deleted_at", "lectures", ["status", "deleted_at"]
        )


def downgrade() -> None:
    op.drop_index("ix_lecture_status_deleted_at", table_name="lectures")
    op.drop_column("lectures", "deleted_at")
