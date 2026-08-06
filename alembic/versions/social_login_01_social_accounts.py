"""소셜 로그인 연결 — social_accounts

Revision ID: social_login_01
Revises: student_interests_01
Create Date: 2026-08-06

카카오·네이버·구글 계정을 학생 계정에 연결한다. (provider, provider_user_id)가 유일키라
같은 소셜 계정이 두 학생에 붙지 못하고, (student_id, provider)가 유일키라 한 학생이 같은
provider를 두 번 연결하지 못한다. provider access token은 저장하지 않으므로 토큰 컬럼이
없다(프로필 1회 조회에만 쓰고 버린다 — app/services/social_auth.py 참고).

student_id는 소프트 참조(FK 없음) — 신규 테이블 규약(collation 정합).

멱등: 테이블이 이미 있으면(운영 DB에 선반영된 경우) 건너뛴다. 그런 환경에서는
`alembic stamp social_login_01`로 적용 표시만 하고 이 upgrade는 실행하지 않는다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "social_login_01"
down_revision: Union[str, None] = "student_interests_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "social_accounts" in insp.get_table_names():
        return
    op.create_table(
        "social_accounts",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("student_id", sa.CHAR(36), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        # provider 식별자 길이는 provider마다 다르다(카카오 숫자, 구글 sub 21자, 네이버 해시).
        # 191은 utf8mb4 + 복합 유니크 인덱스에서 안전한 상한이다.
        sa.Column("provider_user_id", sa.String(191), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        # 같은 소셜 계정이 두 학생에 붙는 것을 DB에서 막는다(계정 탈취 경로 차단).
        sa.UniqueConstraint("provider", "provider_user_id", name="uq_social_provider_user"),
        # 한 학생이 같은 provider를 중복 연결하는 것도 막는다.
        sa.UniqueConstraint("student_id", "provider", name="uq_social_student_provider"),
    )
    op.create_index("ix_social_student", "social_accounts", ["student_id"])


def downgrade() -> None:
    op.drop_table("social_accounts")
