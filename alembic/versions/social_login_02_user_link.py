"""소셜 연결을 콘솔 계정(users)에도 허용 — social_accounts.user_id

Revision ID: social_login_02
Revises: social_login_01
Create Date: 2026-08-07

콘솔 계정(운영자·강사)은 소셜 로그인이 **막혀 있었다**(고권한 계정을 외부 IdP 공격면에
두지 않기 위해). 이 리비전은 그 방침을 유지하면서 예외를 만든다 —
**본인이 비밀번호로 로그인한 뒤 명시적으로 연결한 경우에만** 소셜 로그인을 허용한다.
'이메일이 같으니 자동으로 붙인다'는 하지 않는다(계정 탈취 경로).

그래서 연결 행 자체가 '동의의 증거'다. user_id 가 채워져 있다는 것은 그 계정 소유자가
로그인한 상태에서 연결 버튼을 눌렀다는 뜻이다.

스키마:
- student_id 를 nullable 로 바꾼다(기존 학생 행은 그대로 유지 — 데이터 이관 없음).
- user_id 를 추가하고 (user_id, provider) 유일 인덱스를 건다.
  MySQL 은 NULL 을 유일성 검사에서 제외하므로 학생 행(user_id=NULL)이 아무리 많아도
  서로 충돌하지 않는다.

멱등: 컬럼 존재 검사.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "social_login_02"
down_revision: Union[str, None] = "social_login_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(bind) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns("social_accounts")}


def upgrade() -> None:
    bind = op.get_bind()
    if "social_accounts" not in sa.inspect(bind).get_table_names():
        return  # social_login_01 이 아직 적용되지 않은 환경
    if "user_id" in _cols(bind):
        return

    op.add_column("social_accounts", sa.Column("user_id", sa.CHAR(36), nullable=True))
    op.create_index("ix_social_user", "social_accounts", ["user_id"])
    op.create_unique_constraint(
        "uq_social_user_provider", "social_accounts", ["user_id", "provider"]
    )
    # 학생 행만 있던 시절엔 NOT NULL 이었다 — 콘솔 연결 행은 이 칸이 비므로 완화한다.
    # SQLite 는 ALTER COLUMN 을 지원하지 않지만, 테스트는 create_all 로 최신 모델을
    # 그대로 만들므로 이 분기를 타지 않는다(운영 MySQL 전용 경로).
    if bind.dialect.name != "sqlite":
        op.alter_column(
            "social_accounts", "student_id", existing_type=sa.CHAR(36), nullable=True
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "user_id" not in _cols(bind):
        return
    op.drop_constraint("uq_social_user_provider", "social_accounts", type_="unique")
    op.drop_index("ix_social_user", table_name="social_accounts")
    op.drop_column("social_accounts", "user_id")
