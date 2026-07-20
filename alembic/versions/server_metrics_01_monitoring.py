"""서버 자원 모니터링 스냅샷 — server_metrics (서버별 최신 CPU/메모리/디스크/GPU)

Revision ID: server_metrics_01
Revises: lecture_cp_qid_01
Create Date: 2026-07-21

각 VM(백엔드·DB·GPU STT·프론트)의 최신 지표 1행(server_key 유니크 upsert). 운영 모니터링
대시보드의 원천. 멱등: 테이블 존재 검사.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "server_metrics_01"
down_revision: Union[str, None] = "lecture_cp_qid_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "server_metrics" in insp.get_table_names():
        return
    op.create_table(
        "server_metrics",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("server_key", sa.String(40), nullable=False),
        sa.Column("label", sa.String(60), nullable=False),
        sa.Column("host", sa.String(80), nullable=True),
        sa.Column("cpu_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("cpu_cores", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("load1", sa.Float(), nullable=True),
        sa.Column("mem_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("mem_used_mb", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mem_total_mb", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("disk_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("disk_used_gb", sa.Float(), nullable=False, server_default="0"),
        sa.Column("disk_total_gb", sa.Float(), nullable=False, server_default="0"),
        sa.Column("gpu_present", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("gpu_name", sa.String(80), nullable=True),
        sa.Column("gpu_util_pct", sa.Float(), nullable=True),
        sa.Column("gpu_mem_used_mb", sa.Integer(), nullable=True),
        sa.Column("gpu_mem_total_mb", sa.Integer(), nullable=True),
        sa.Column("collected_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_server_metrics_key", "server_metrics", ["server_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_server_metrics_key", table_name="server_metrics")
    op.drop_table("server_metrics")
