"""서버 지표 시계열 표본 — server_metric_samples (추이 그래프, append-only)

Revision ID: server_samples_01
Revises: server_metrics_01
Create Date: 2026-07-21

수집 때마다 append. 대시보드가 서버별 최근 구간을 라인 차트로. 보존창 밖은 인제스트 때 정리.
멱등: 테이블 존재 검사.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "server_samples_01"
down_revision: Union[str, None] = "server_metrics_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "server_metric_samples" in insp.get_table_names():
        return
    op.create_table(
        "server_metric_samples",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("server_key", sa.String(40), nullable=False),
        sa.Column("cpu_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("mem_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("gpu_util_pct", sa.Float(), nullable=True),
        sa.Column("collected_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_sms_key", "server_metric_samples", ["server_key"])
    op.create_index("ix_sms_key_time", "server_metric_samples", ["server_key", "collected_at"])


def downgrade() -> None:
    op.drop_index("ix_sms_key_time", table_name="server_metric_samples")
    op.drop_index("ix_sms_key", table_name="server_metric_samples")
    op.drop_table("server_metric_samples")
