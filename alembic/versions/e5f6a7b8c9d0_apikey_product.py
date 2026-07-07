"""api_keys product/subject/label (메인·교육형 캡차 API 구분)

캡차 API를 제품(메인/교육형)·과목별로 발급하기 위한 컬럼. 비파괴적.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has(bind, table: str, col: str) -> bool:
    return any(c["name"] == col for c in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has(bind, "api_keys", "product"):
        op.add_column("api_keys", sa.Column("product", sa.String(20), nullable=False, server_default="captcha"))
    if not _has(bind, "api_keys", "subject"):
        op.add_column("api_keys", sa.Column("subject", sa.String(20), nullable=True))
    if not _has(bind, "api_keys", "label"):
        op.add_column("api_keys", sa.Column("label", sa.String(100), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    for col in ("label", "subject", "product"):
        if _has(bind, "api_keys", col):
            op.drop_column("api_keys", col)
