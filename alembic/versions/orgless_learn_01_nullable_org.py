"""무소속(이메일 가입) 학생 학습기록 허용 — 학습 3테이블 organization_id NULL 완화

Revision ID: orgless_learn_01
Revises: bank_srs_01
Create Date: 2026-07-19

기관 은퇴(제품 전환) 후 학생은 무소속(이메일 가입, organization_id 없음)이 정식이다.
그런데 learning_attempts·student_progress·wrong_answers 의 organization_id 가
NOT NULL이라, 무소속 학생의 첫 채점 저장이 1048(Column cannot be null)로 터져
verify 전체가 409로 실패했다(0719 문제은행 SRS 라이브 검증에서 실증 — 로스터
학생은 전원 org가 있어 그동안 안 드러났다). 알려진 백로그('org_id NOT NULL 완화')의
학습 축 실행이다.

멱등: 이미 NULL 허용이면 건너뛴다. 기존 행 무변경(값은 그대로).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "orgless_learn_01"
down_revision: Union[str, None] = "bank_srs_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("learning_attempts", "student_progress", "wrong_answers")


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for table in _TABLES:
        col = next(c for c in insp.get_columns(table) if c["name"] == "organization_id")
        if not col["nullable"]:
            op.alter_column(
                table, "organization_id", existing_type=sa.CHAR(36), nullable=True
            )


def downgrade() -> None:
    # 되돌리려면 NULL 행을 먼저 채워야 한다(무소속 학생 기록) — 실제로는 되돌릴 일 없음
    for table in _TABLES:
        op.alter_column(table, "organization_id", existing_type=sa.CHAR(36), nullable=False)
