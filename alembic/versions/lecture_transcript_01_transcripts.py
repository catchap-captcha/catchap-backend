"""강의 전사(자막) — lecture_transcripts(강사 제공 SRT/VTT/붙여넣기 또는 자동 STT 캐시)

Revision ID: lecture_transcript_01
Revises: ai_model_cfg_01
Create Date: 2026-07-20

강사가 이미 가진 자막을 받아 LLM 문항 생성에 쓰고(자동 STT 대체), 자동 STT 결과도 캐시한다.
lectures 행을 가볍게 유지하려 1:1 분리 테이블. 멱등: 존재 검사 후 건너뛴다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "lecture_transcript_01"
down_revision: Union[str, None] = "ai_model_cfg_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "lecture_transcripts" in insp.get_table_names():
        return
    op.create_table(
        "lecture_transcripts",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("lecture_id", sa.CHAR(36), nullable=False),
        sa.Column("segments", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("segment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("lecture_id", name="uq_lecture_transcript"),
    )


def downgrade() -> None:
    op.drop_table("lecture_transcripts")
