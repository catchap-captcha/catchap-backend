"""student_profiles.interests (관심사 온보딩)

최초 로그인 시 고른 코스 분류(category) 목록을 담는다. null=아직 안 골랐음(온보딩 모달 노출),
[]=골랐으나 스킵, ["IT/개발", ...]=관심사. 홈 추천 강의의 기준. nullable이라 비파괴적.

Revision ID: student_interests_01
Revises: lecture_botsusp_01
Create Date: 2026-07-31
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "student_interests_01"
down_revision: Union[str, None] = "lecture_botsusp_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has(bind, table: str, col: str) -> bool:
    return any(c["name"] == col for c in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has(bind, "student_profiles", "interests"):
        op.add_column(
            "student_profiles",
            sa.Column("interests", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has(bind, "student_profiles", "interests"):
        op.drop_column("student_profiles", "interests")
