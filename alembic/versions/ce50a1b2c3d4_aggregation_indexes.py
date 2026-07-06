"""aggregation composite indexes

집계 쿼리(대시보드) 가속용 복합 인덱스. 결과·동작 변화 없음, 조회 속도만 개선.
- 학생별 기간 집계: (student_id, created_at)
- 기관별 기간 집계: (organization_id, created_at)

Revision ID: ce50a1b2c3d4
Revises: bd27a963365f
Create Date: 2026-07-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "ce50a1b2c3d4"
down_revision: Union[str, None] = "bd27a963365f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _index_exists(bind, table: str, name: str) -> bool:
    rows = bind.execute(
        sa.text(f"SHOW INDEX FROM {table} WHERE Key_name = :n"), {"n": name}
    ).fetchall()
    return bool(rows)


def upgrade() -> None:
    bind = op.get_bind()
    plan = [
        ("learning_attempts", "ix_la_student_created", ["student_id", "created_at"]),
        ("learning_attempts", "ix_la_org_created", ["organization_id", "created_at"]),
        ("api_usage_logs", "ix_aul_org_created", ["organization_id", "created_at"]),
    ]
    for table, name, cols in plan:
        if not _index_exists(bind, table, name):
            op.create_index(name, table, cols)


def downgrade() -> None:
    bind = op.get_bind()
    for table, name in [
        ("learning_attempts", "ix_la_student_created"),
        ("learning_attempts", "ix_la_org_created"),
        ("api_usage_logs", "ix_aul_org_created"),
    ]:
        if _index_exists(bind, table, name):
            op.drop_index(name, table_name=table)
