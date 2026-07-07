"""behavior_summaries dataset_status (아동용 캡차 학습셋 큐레이션)

운영 콘솔 행동 데이터 화면에서 학습 데이터셋 포함/제외를 관리하기 위한 컬럼. 비파괴적.

Revision ID: a7b8c9d0e1f2
Revises: f4a5b6c7d8e9
Create Date: 2026-07-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f4a5b6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has(bind, table: str, col: str) -> bool:
    return any(c["name"] == col for c in sa.inspect(bind).get_columns(table))


def _has_index(bind, table: str, name: str) -> bool:
    return any(ix["name"] == name for ix in sa.inspect(bind).get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has(bind, "behavior_summaries", "dataset_status"):
        op.add_column(
            "behavior_summaries",
            sa.Column("dataset_status", sa.String(20), nullable=False, server_default="candidate"),
        )
    # 운영 콘솔 목록(created_at DESC 정렬 + 기간 필터) 가속
    if not _has_index(bind, "behavior_summaries", "ix_bs_created"):
        op.create_index("ix_bs_created", "behavior_summaries", ["created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_index(bind, "behavior_summaries", "ix_bs_created"):
        op.drop_index("ix_bs_created", table_name="behavior_summaries")
    if _has(bind, "behavior_summaries", "dataset_status"):
        op.drop_column("behavior_summaries", "dataset_status")
