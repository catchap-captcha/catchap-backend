"""class_assignments — 반 배정 이력(SIS enrollment) + 현 배정 백필

Revision ID: f5a6b7c8d9e0
Revises: e3f4a5b6c7d8
Create Date: 2026-07-13
"""
import uuid
from datetime import date, datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f5a6b7c8d9e0"
down_revision: Union[str, None] = "e3f4a5b6c7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _school_year_start(today: date) -> date:
    year = today.year if today.month >= 3 else today.year - 1
    return date(year, 3, 1)


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("class_assignments"):
        return
    op.create_table(
        "class_assignments",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("organization_id", sa.CHAR(36), nullable=False, index=True),
        sa.Column("student_id", sa.CHAR(36), sa.ForeignKey("student_profiles.id"), nullable=False),
        sa.Column("class_id", sa.CHAR(36), sa.ForeignKey("classes.id"), nullable=False),
        sa.Column("started_on", sa.DateTime(), nullable=False),
        sa.Column("ended_on", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_ca_student", "class_assignments", ["student_id"])
    op.create_index("ix_ca_class", "class_assignments", ["class_id"])
    op.create_index("ix_ca_org", "class_assignments", ["organization_id"])

    # 백필: 현재 배정된 학생은 '학년도 시작(3/1)'을 시작일로 하는 열린 행 생성.
    # (과거 정확한 배정일은 소실 — 달력 근사. 이후 배정 변경은 실시각으로 기록된다.)
    sys = datetime.combine(_school_year_start(date.today()), datetime.min.time())
    now = datetime.now()
    rows = bind.execute(
        sa.text(
            "SELECT id, organization_id, class_id FROM student_profiles "
            "WHERE class_id IS NOT NULL AND status != 'disabled'"
        )
    ).fetchall()
    for sid, org_id, cls_id in rows:
        bind.execute(
            sa.text(
                "INSERT INTO class_assignments "
                "(id, organization_id, student_id, class_id, started_on, ended_on, created_at, updated_at) "
                "VALUES (:id, :org, :sid, :cid, :start, NULL, :now, :now)"
            ),
            {"id": str(uuid.uuid4()), "org": org_id, "sid": sid, "cid": cls_id,
             "start": sys, "now": now},
        )


def downgrade() -> None:
    op.drop_table("class_assignments")
