"""lecture_watch_progress.checkpoint_fails — 오답 상한(되감기) 카운터

한 체크포인트에서 연속 오답 횟수를 센다. lecture_service.MAX_CHECKPOINT_FAILS에
닿으면 그 대목을 다시 보도록 watched_max를 되감고 0으로 리셋한다. 기존 행은 0.

Revision ID: lecture_fail_01
Revises: student_email_01
"""

import sqlalchemy as sa
from alembic import op

revision = "lecture_fail_01"
down_revision = "student_email_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "lecture_watch_progress",
        sa.Column(
            "checkpoint_fails",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("lecture_watch_progress", "checkpoint_fails")
