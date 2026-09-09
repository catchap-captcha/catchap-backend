"""비동기 행동데이터 내보내기 작업 테이블.

Revision ID: behavior_export_01
Revises: motion_samples_01
"""
import sqlalchemy as sa
from alembic import op

revision = "behavior_export_01"
down_revision = "motion_samples_01"
branch_labels = None
depends_on = None

_TABLE = "behavior_export_jobs"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("requested_by", sa.CHAR(36), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("phase", sa.String(24), nullable=True),
        sa.Column("filters_json", sa.JSON(), nullable=False),
        sa.Column("purpose", sa.String(255), nullable=False),
        sa.Column("dua_acknowledged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("snapshot_at", sa.DateTime(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("k_dropped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("object_key", sa.String(500), nullable=True),
        sa.Column("file_name", sa.String(255), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"), server_onupdate=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("requested_by", "idempotency_key", name="uq_behavior_export_actor_idem"),
    )
    op.create_index("ix_behavior_export_requested_by", _TABLE, ["requested_by"])
    op.create_index("ix_behavior_export_status", _TABLE, ["status"])


def downgrade() -> None:
    op.drop_index("ix_behavior_export_status", table_name=_TABLE)
    op.drop_index("ix_behavior_export_requested_by", table_name=_TABLE)
    op.drop_table(_TABLE)
