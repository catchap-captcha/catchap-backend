"""코스 카테고리 — courses.category (브라우징용 대분류, 학교식 과목 대체)

Revision ID: course_category_01
Revises: server_samples_01
Create Date: 2026-07-21

코스 중심 전환(사용자 결정 2026-07-21): 학교식 rigid '과목'(국어·수학...)을 화면에서 걷어내고,
코스에 선택적 '카테고리'(법정의무·자격증·어학 등)를 둬 카탈로그를 브라우징한다(실무 표준 =
코스가 상품 단위, 카테고리가 분류 축). subject 컬럼은 학습기록·행동데이터·문제은행 정합을 위해
유지하고 기본값('일반')을 채운다. 멱등: 컬럼 존재 검사.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "course_category_01"
down_revision: Union[str, None] = "server_samples_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = [c["name"] for c in insp.get_columns("courses")]
    if "category" not in cols:
        op.add_column("courses", sa.Column("category", sa.String(40), nullable=True))


def downgrade() -> None:
    op.drop_column("courses", "category")
