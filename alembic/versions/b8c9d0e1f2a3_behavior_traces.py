"""behavior_traces — 원시 포인터 궤적 (아동용 캡차 판정 모델 학습 재료)

behavior_summaries 1행당 최대 1행. 비파괴적.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-07-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, table: str) -> bool:
    return sa.inspect(bind).has_table(table)


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "behavior_traces"):
        op.create_table(
            "behavior_traces",
            sa.Column("id", sa.CHAR(36), primary_key=True),
            sa.Column("behavior_id", sa.CHAR(36), nullable=False),
            sa.Column("points", sa.JSON(), nullable=False),
            sa.Column("point_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("box_w", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("box_h", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            # collation 명시 금지 — 기존 테이블들(DB 기본 utf8mb4_0900_ai_ci)과 달라지면
            # behavior_summaries.id 와의 JOIN이 collation 충돌로 깨진다
            mysql_engine="InnoDB",
        )
        op.create_index("ix_behavior_traces_behavior_id", "behavior_traces", ["behavior_id"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "behavior_traces"):
        op.drop_table("behavior_traces")
