"""behavior_summaries — input_type(입력방식) + sample_label(정답 라벨 자리)

아동용 캡차 판정 모델 학습 대비. 둘 다 수집 시점에만 채울 수 있어(소급 불가)
지금부터 저장한다. 비파괴적·멱등.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-07-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    if not insp.has_table(table):
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "behavior_summaries", "input_type"):
        op.add_column(
            "behavior_summaries",
            sa.Column("input_type", sa.String(10), nullable=False, server_default="unknown"),
        )
    if not _has_column(bind, "behavior_summaries", "sample_label"):
        op.add_column(
            "behavior_summaries",
            sa.Column("sample_label", sa.String(12), nullable=False, server_default="organic"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "behavior_summaries", "sample_label"):
        op.drop_column("behavior_summaries", "sample_label")
    if _has_column(bind, "behavior_summaries", "input_type"):
        op.drop_column("behavior_summaries", "input_type")
