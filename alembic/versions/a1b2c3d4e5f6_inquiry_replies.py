"""inquiry replies (1문의 : N답변)

운영자가 한 문의에 확인 후 여러 번 답변할 수 있도록 inquiry_replies 테이블 신설.
기존 단일 답변(inquiries.answer)은 첫 답변으로 백필한 뒤 컬럼 제거.

Revision ID: a1b2c3d4e5f6
Revises: f3a4b5c6d7e8
Create Date: 2026-07-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_col(bind, table: str, col: str) -> bool:
    return any(c["name"] == col for c in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("inquiry_replies"):
        op.create_table(
            "inquiry_replies",
            sa.Column("id", sa.CHAR(36), primary_key=True),
            sa.Column(
                "inquiry_id", sa.CHAR(36), sa.ForeignKey("inquiries.id"), nullable=False, index=True
            ),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("answered_by", sa.CHAR(36), nullable=True),
            sa.Column("email_status", sa.String(20), nullable=False, server_default="sent"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )

    # 기존 단일 답변을 첫 답변 레코드로 이관
    if _has_col(bind, "inquiries", "answer"):
        op.execute(
            sa.text(
                "INSERT INTO inquiry_replies "
                "(id, inquiry_id, body, answered_by, email_status, created_at, updated_at) "
                "SELECT UUID(), id, answer, answered_by, 'sent', "
                "COALESCE(answered_at, NOW()), COALESCE(answered_at, NOW()) "
                "FROM inquiries WHERE answer IS NOT NULL AND answer <> ''"
            )
        )

    for col in ("answered_by", "answered_at", "answer"):
        if _has_col(bind, "inquiries", col):
            op.drop_column("inquiries", col)


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_col(bind, "inquiries", "answer"):
        op.add_column("inquiries", sa.Column("answer", sa.Text(), nullable=True))
    if not _has_col(bind, "inquiries", "answered_at"):
        op.add_column("inquiries", sa.Column("answered_at", sa.DateTime(), nullable=True))
    if not _has_col(bind, "inquiries", "answered_by"):
        op.add_column("inquiries", sa.Column("answered_by", sa.CHAR(36), nullable=True))
    if sa.inspect(bind).has_table("inquiry_replies"):
        op.drop_table("inquiry_replies")
