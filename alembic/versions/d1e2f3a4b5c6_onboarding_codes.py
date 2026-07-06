"""onboarding join/invite codes

학생 가입 코드(student_join_codes) + 학부모 초대 코드(parent_invite_codes) 테이블 신설.
새 테이블 생성만 하므로 비파괴적. (온보딩 재설계 · B1 해소)

Revision ID: d1e2f3a4b5c6
Revises: ce50a1b2c3d4
Create Date: 2026-07-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, None] = "ce50a1b2c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "student_join_codes"):
        op.create_table(
            "student_join_codes",
            sa.Column("id", sa.CHAR(36), primary_key=True),
            sa.Column("organization_id", sa.CHAR(36), nullable=False, index=True),
            sa.Column("class_id", sa.CHAR(36), nullable=True, index=True),
            sa.Column("login_id", sa.String(60), nullable=False),
            sa.Column("code_hash", sa.String(64), nullable=False, index=True),
            sa.Column("class_label", sa.String(60), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("used_at", sa.DateTime(), nullable=True),
            sa.Column("student_id", sa.CHAR(36), nullable=True),
            sa.Column("created_by", sa.CHAR(36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("login_id", name="uq_sjc_login_id"),
            mysql_charset="utf8mb4",
            mysql_engine="InnoDB",
        )
    if not _has_table(bind, "parent_invite_codes"):
        op.create_table(
            "parent_invite_codes",
            sa.Column("id", sa.CHAR(36), primary_key=True),
            sa.Column("student_id", sa.CHAR(36), nullable=False, index=True),
            sa.Column("organization_id", sa.CHAR(36), nullable=False, index=True),
            sa.Column("code_hash", sa.String(64), nullable=False, index=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("max_uses", sa.Integer(), nullable=False, server_default="2"),
            sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.Column("created_by", sa.CHAR(36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            mysql_charset="utf8mb4",
            mysql_engine="InnoDB",
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "parent_invite_codes"):
        op.drop_table("parent_invite_codes")
    if _has_table(bind, "student_join_codes"):
        op.drop_table("student_join_codes")
