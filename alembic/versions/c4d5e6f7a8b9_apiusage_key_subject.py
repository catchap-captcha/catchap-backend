"""api_usage_logs.api_key_id + product + subject (키별·과목별 사용량 집계)

Revision ID: c4d5e6f7a8b9
Revises: b1c2d3e4f5a6
Create Date: 2026-07-10
"""

import sqlalchemy as sa
from alembic import op

revision = "c4d5e6f7a8b9"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("api_usage_logs", sa.Column("api_key_id", sa.CHAR(36), nullable=True))
    op.add_column("api_usage_logs", sa.Column("product", sa.String(20), nullable=True))
    op.add_column("api_usage_logs", sa.Column("subject", sa.String(20), nullable=True))
    op.create_index("ix_aul_api_key_id", "api_usage_logs", ["api_key_id"])


def downgrade() -> None:
    op.drop_index("ix_aul_api_key_id", table_name="api_usage_logs")
    op.drop_column("api_usage_logs", "subject")
    op.drop_column("api_usage_logs", "product")
    op.drop_column("api_usage_logs", "api_key_id")
