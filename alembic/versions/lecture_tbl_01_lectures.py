"""lectures — 강의 시청 검증 도메인(강의 메타·확인 문항·시청 진행·체크포인트 이력)

Revision ID: lecture_tbl_01
Revises: scratch_tbl_01
Create Date: 2026-07-15

영상 파일 경로는 저장하지 않는다(LECTURE_MEDIA_DIR/{id}{video_ext}로 유도 — 경로조작 차단).
문항 정답(answer_index)은 payload(JSON)와 분리 컬럼 — 응답 직렬화 시 정답 유출을 구조적으로 막는다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "lecture_tbl_01"
down_revision: Union[str, None] = "scratch_tbl_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("lectures"):
        op.create_table(
            "lectures",
            sa.Column("id", sa.CHAR(36), primary_key=True),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("subject", sa.String(20), nullable=False),
            sa.Column("video_ext", sa.String(10), nullable=False),
            sa.Column("video_bytes", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("duration_sec", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("check_min_sec", sa.Integer(), nullable=False, server_default="60"),
            sa.Column("check_max_sec", sa.Integer(), nullable=False, server_default="180"),
            sa.Column("status", sa.String(20), nullable=False, server_default="active"),
            sa.Column("uploaded_by", sa.CHAR(36), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_lecture_subject_status", "lectures", ["subject", "status"])
        op.create_index("ix_lecture_created", "lectures", ["created_at"])

    if not insp.has_table("lecture_questions"):
        op.create_table(
            "lecture_questions",
            sa.Column("id", sa.CHAR(36), primary_key=True),
            sa.Column(
                "lecture_id", sa.CHAR(36), sa.ForeignKey("lectures.id"), nullable=False
            ),
            sa.Column("position_sec", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("answer_index", sa.Integer(), nullable=False),
            sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
            sa.Column("status", sa.String(20), nullable=False, server_default="active"),
            sa.Column("order_no", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_lq_lecture", "lecture_questions", ["lecture_id"])
        op.create_index("ix_lq_lecture_pos", "lecture_questions", ["lecture_id", "position_sec"])

    if not insp.has_table("lecture_watch_progress"):
        op.create_table(
            "lecture_watch_progress",
            sa.Column("id", sa.CHAR(36), primary_key=True),
            sa.Column(
                "student_id", sa.CHAR(36), sa.ForeignKey("student_profiles.id"), nullable=False
            ),
            sa.Column(
                "lecture_id", sa.CHAR(36), sa.ForeignKey("lectures.id"), nullable=False
            ),
            sa.Column("watched_max_sec", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("next_checkpoint_sec", sa.Integer(), nullable=True),
            sa.Column("checkpoints_passed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(20), nullable=False, server_default="watching"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("student_id", "lecture_id", name="uq_lecture_watch"),
        )
        op.create_index("ix_lwp_student", "lecture_watch_progress", ["student_id"])
        op.create_index("ix_lwp_lecture", "lecture_watch_progress", ["lecture_id"])

    if not insp.has_table("lecture_checkpoint_events"):
        op.create_table(
            "lecture_checkpoint_events",
            sa.Column("id", sa.CHAR(36), primary_key=True),
            sa.Column(
                "student_id", sa.CHAR(36), sa.ForeignKey("student_profiles.id"), nullable=False
            ),
            sa.Column(
                "lecture_id", sa.CHAR(36), sa.ForeignKey("lectures.id"), nullable=False
            ),
            sa.Column("position_sec", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("result", sa.String(20), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index(
            "ix_lce_student_created", "lecture_checkpoint_events", ["student_id", "created_at"]
        )
        op.create_index("ix_lce_lecture", "lecture_checkpoint_events", ["lecture_id"])


def downgrade() -> None:
    op.drop_table("lecture_checkpoint_events")
    op.drop_table("lecture_watch_progress")
    op.drop_table("lecture_questions")
    op.drop_table("lectures")
