"""lecture_materials — 강의 자료실(파일/링크) + lectures.order_no(과목 내 목차 순서)

Revision ID: lecture_mat_01
Revises: lecture_beh_01
Create Date: 2026-07-15

- lecture_materials: file 종류는 경로를 저장하지 않는다(LECTURE_MEDIA_DIR/materials/{id}{file_ext}로
  유도 — 영상과 동일한 경로조작 차단 원칙). link 종류는 url에 외부 URL 원문.
- lectures.order_no: 목차 정렬 (subject, order_no, created_at)의 축. 기존 행은 0(=created_at 순 유지).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "lecture_mat_01"
down_revision: Union[str, None] = "lecture_beh_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("lecture_materials"):
        op.create_table(
            "lecture_materials",
            sa.Column("id", sa.CHAR(36), primary_key=True),
            sa.Column(
                "lecture_id", sa.CHAR(36), sa.ForeignKey("lectures.id"), nullable=False
            ),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("kind", sa.String(10), nullable=False),
            sa.Column("url", sa.String(500), nullable=False),
            sa.Column("file_ext", sa.String(10), nullable=True),
            sa.Column("file_bytes", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("order_no", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(20), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_lm_lecture", "lecture_materials", ["lecture_id"])
        op.create_index("ix_lm_lecture_order", "lecture_materials", ["lecture_id", "order_no"])

    cols = {c["name"] for c in insp.get_columns("lectures")}
    if "order_no" not in cols:
        op.add_column(
            "lectures",
            sa.Column("order_no", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    op.drop_column("lectures", "order_no")
    op.drop_table("lecture_materials")
