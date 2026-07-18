"""강사 코스 — courses 테이블 + lectures.course_id

Revision ID: course_tbl_01
Revises: behavior_actor_01
Create Date: 2026-07-18

강사 코스 모델(사용자 결정 0718): 한 강사가 한 과목으로 강의를 묶는 '코스'(예:
'수학 기초반'). 코스=과목 하나 고정. 학생 화면은 과목 → 강사별 코스 → 강의 순서.

- courses: instructor_id·subject·title·description·order_no·status.
- lectures.course_id NULL 허용: 코스 도입 전 기존 강의는 미분류(NULL). 기존 행 무변경.
멱등: 테이블·컬럼 존재를 검사해 이미 적용됐으면 건너뛴다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "course_tbl_01"
down_revision: Union[str, None] = "behavior_actor_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # FK 제약을 걸지 않는다(소프트 참조 — behavior_summaries.student_id와 같은 규약).
    # DB FK는 참조/피참조 컬럼의 collation 일치를 강제하는데, 라이브와 로컬의 기본
    # collation이 다를 수 있어(라이브 동기화 시 재생성한 DB) 생성이 실패한다. 무결성은
    # 애플리케이션(엔드포인트의 소유·존재 검증)이 담당한다.
    if "courses" not in insp.get_table_names():
        op.create_table(
            "courses",
            sa.Column("id", sa.CHAR(36), primary_key=True),
            sa.Column("instructor_id", sa.CHAR(36), nullable=False),
            sa.Column("subject", sa.String(20), nullable=False),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("order_no", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(20), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_courses_instructor_id", "courses", ["instructor_id"])
        op.create_index("ix_course_subject_status", "courses", ["subject", "status"])

    lec_cols = {c["name"] for c in insp.get_columns("lectures")}
    if "course_id" not in lec_cols:
        op.add_column("lectures", sa.Column("course_id", sa.CHAR(36), nullable=True))
        op.create_index("ix_lectures_course_id", "lectures", ["course_id"])


def downgrade() -> None:
    op.drop_index("ix_lectures_course_id", "lectures")
    op.drop_column("lectures", "course_id")
    op.drop_index("ix_course_subject_status", "courses")
    op.drop_index("ix_courses_instructor_id", "courses")
    op.drop_table("courses")
