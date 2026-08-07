"""add password_reset_expires_at to users (temp password expiry)

Revision ID: instructor_temp_pw_expiry_01
Revises: course_thumbnail_01
Create Date: 2026-08-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "instructor_temp_pw_expiry_01"
down_revision: Union[str, None] = "course_thumbnail_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_col(bind, table: str, col: str) -> bool:
    return any(c["name"] == col for c in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_col(bind, "users", "password_reset_expires_at"):
        op.add_column(
            "users",
            sa.Column("password_reset_expires_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_col(bind, "users", "password_reset_expires_at"):
        op.drop_column("users", "password_reset_expires_at")
