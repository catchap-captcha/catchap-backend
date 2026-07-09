"""api_keys.first_party + organizations.edu_subjects (과목별 판매 스코프)

Revision ID: b1c2d3e4f5a6
Revises: a8b9c0d1e2f3
Create Date: 2026-07-10

- api_keys.first_party: 1st-party(우리 인앱) 키만 ?subject= 오버라이드 허용. 외부 판매 키는
  발급 과목에 고정(구매 안 한 과목 접근 차단).
- organizations.edu_subjects: 기관이 구매한 교육형 과목 목록(외부 키 발급 허용 범위).
"""

import sqlalchemy as sa
from alembic import op

revision = "b1c2d3e4f5a6"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column("first_party", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "organizations",
        sa.Column("edu_subjects", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organizations", "edu_subjects")
    op.drop_column("api_keys", "first_party")
