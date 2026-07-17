"""가입 연령 분기 — 생년월일·보호자 이메일 수집 + 무소속 가입 동의 허용

Revision ID: signup_age_01
Revises: tz_kst_01
Create Date: 2026-07-17

- student_profiles.birth_date DATE NULL: 신규 가입은 필수 수집(만 14세 판정 기준),
  구계정(학교 경유)은 NULL 유지.
- student_profiles.guardian_email VARCHAR(255) NULL: 만 14세 미만 가입의 법정대리인
  동의 증빙·철회 연락처. 성인·구계정은 NULL.
- consents.organization_id / granted_by_user_id → NULL 허용: 무소속(이메일 가입)
  학생의 가입 동의(signup_guardian)는 기관도, 보호자 계정도 없다. 기존 행 무변경.
멱등: 컬럼 상태를 검사해 이미 적용됐으면 건너뛴다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "signup_age_01"
down_revision: Union[str, None] = "tz_kst_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    sp_cols = {c["name"] for c in insp.get_columns("student_profiles")}
    if "birth_date" not in sp_cols:
        op.add_column("student_profiles", sa.Column("birth_date", sa.Date(), nullable=True))
    if "guardian_email" not in sp_cols:
        op.add_column(
            "student_profiles", sa.Column("guardian_email", sa.String(length=255), nullable=True)
        )

    con_cols = {c["name"]: c for c in insp.get_columns("consents")}
    for name in ("organization_id", "granted_by_user_id"):
        col = con_cols.get(name)
        if col is not None and not col.get("nullable", False):
            op.alter_column("consents", name, existing_type=sa.CHAR(36), nullable=True)


def downgrade() -> None:
    # 주의: NULL organization_id/granted_by_user_id 동의 행이 있으면 NOT NULL 복원이
    # 실패한다(해당 행 정리 후 실행). 컬럼 드롭은 수집된 생년월일·보호자 이메일을 지운다.
    for name in ("granted_by_user_id", "organization_id"):
        op.alter_column("consents", name, existing_type=sa.CHAR(36), nullable=False)
    op.drop_column("student_profiles", "guardian_email")
    op.drop_column("student_profiles", "birth_date")
