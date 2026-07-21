"""서버 지표 시간별 롤업 — server_metric_hourly (주간·월간 추이 집계)

Revision ID: server_hourly_01
Revises: course_category_01
Create Date: 2026-07-21

raw 표본(30초)은 단기(48h)만 두고, 장기(주/월)는 시간 버킷 1행에 합계·개수로 누적해
평균(sum/count)으로 그린다. 시간당 1행이라 30일이어도 서버당 720행 수준. 멱등: 테이블 존재 검사.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "server_hourly_01"
down_revision: Union[str, None] = "course_category_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "server_metric_hourly" in insp.get_table_names():
        return
    op.create_table(
        "server_metric_hourly",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("server_key", sa.String(40), nullable=False),
        sa.Column("hour", sa.DateTime(), nullable=False),
        sa.Column("samples", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cpu_sum", sa.Float(), nullable=False, server_default="0"),
        sa.Column("mem_sum", sa.Float(), nullable=False, server_default="0"),
        sa.Column("gpu_sum", sa.Float(), nullable=False, server_default="0"),
        sa.Column("gpu_samples", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_smh_key", "server_metric_hourly", ["server_key"])
    op.create_index("ix_smh_key_hour", "server_metric_hourly", ["server_key", "hour"])


def downgrade() -> None:
    op.drop_index("ix_smh_key_hour", table_name="server_metric_hourly")
    op.drop_index("ix_smh_key", table_name="server_metric_hourly")
    op.drop_table("server_metric_hourly")
