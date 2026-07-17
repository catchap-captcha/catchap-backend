"""system_settings — 운영자 입력 전역 설정(AI API 키 등, 값은 Fernet 암호문)

.env 재배포 없이 운영자가 콘솔에서 키를 넣으면 기능이 켜져야 한다(STT·LLM 문항 생성).
value는 settings_service가 항상 암호화해 저장한다 — DB 유출이 곧 키 유출이 되지 않게.

Revision ID: sys_settings_01
Revises: lecture_rewind_01
"""

import sqlalchemy as sa
from alembic import op

revision = "sys_settings_01"
down_revision = "lecture_rewind_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "system_settings" in sa.inspect(bind).get_table_names():
        return  # 멱등 — 재실행 안전
    op.create_table(
        "system_settings",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("key", sa.String(60), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.CHAR(36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("key", name="uq_system_setting_key"),
    )
    op.create_index("ix_system_settings_key", "system_settings", ["key"])


def downgrade() -> None:
    op.drop_table("system_settings")
