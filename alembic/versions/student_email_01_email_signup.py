"""학생 이메일 가입 전환 — student_profiles.organization_id nullable + login_id 255자

Revision ID: student_email_01
Revises: lecture_multi_01
Create Date: 2026-07-16

- organization_id → NULL 허용: 이메일 가입 학생은 무소속(None). 기존 행 무변경.
- student_login_id → String(255): 이메일이 로그인 아이디가 될 수 있어 종전 50자 확장.
  유니크 인덱스는 MODIFY로 유지된다. 기존 데이터 무변경.
멱등: 컬럼 상태를 검사해 이미 적용됐으면 건너뛴다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "student_email_01"
down_revision: Union[str, None] = "lecture_multi_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    cols = {c["name"]: c for c in insp.get_columns("student_profiles")}

    org = cols.get("organization_id")
    if org is not None and not org.get("nullable", False):
        op.alter_column(
            "student_profiles",
            "organization_id",
            existing_type=sa.CHAR(36),
            nullable=True,
        )

    login = cols.get("student_login_id")
    login_len = getattr(login.get("type") if login else None, "length", None)
    if login is not None and (login_len is None or login_len < 255):
        op.alter_column(
            "student_profiles",
            "student_login_id",
            existing_type=sa.String(length=50),
            type_=sa.String(length=255),
            existing_nullable=False,
        )


def downgrade() -> None:
    # 주의: NULL organization_id 행·50자 초과 login_id가 있으면 실패한다(데이터 정리 후 실행).
    op.alter_column(
        "student_profiles",
        "student_login_id",
        existing_type=sa.String(length=255),
        type_=sa.String(length=50),
        existing_nullable=False,
    )
    op.alter_column(
        "student_profiles",
        "organization_id",
        existing_type=sa.CHAR(36),
        nullable=False,
    )
