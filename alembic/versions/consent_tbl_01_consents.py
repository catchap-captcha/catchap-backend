"""consents — 아동 개인정보 처리 동의(보호자 법정대리인) 기록 (Group B #58)

Revision ID: consent_tbl_01
Revises: la_graded_001
Create Date: 2026-07-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "consent_tbl_01"
down_revision: Union[str, None] = "la_graded_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("consents"):
        return
    op.create_table(
        "consents",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("student_id", sa.CHAR(36), sa.ForeignKey("student_profiles.id"), nullable=False),
        sa.Column("organization_id", sa.CHAR(36), nullable=False),
        sa.Column("granted_by_user_id", sa.CHAR(36), nullable=False),
        sa.Column("consent_type", sa.String(40), nullable=False, server_default="personal_info"),
        sa.Column("terms_version", sa.String(20), nullable=False, server_default="v1"),
        sa.Column("granted_at", sa.DateTime(), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_consent_student", "consents", ["student_id"])
    op.create_index("ix_consent_org", "consents", ["organization_id"])
    op.create_index("ix_consent_grantor", "consents", ["granted_by_user_id"])


def downgrade() -> None:
    op.drop_table("consents")
