"""lecture_watch_progress — 행동 기반 캡차 트리거 컬럼(세션·면제·의심)

Revision ID: lecture_beh_01
Revises: lecture_tbl_01
Create Date: 2026-07-15

- session_id / last_heartbeat_at: 학생당 단일 활성 시청 세션 강제(동시접속 차단 —
  「사업주 직업능력개발훈련 지원규정」 별표1의 동일 ID 동시접속 방지 요건).
- exempt_streak: 체크포인트 도달 시 상호작용(자기신고) 캡차 면제의 연속 횟수(상한 2, 통과 시 리셋).
- suspicion: 의심 이벤트 누적(안 본 구간 seek/과속/탭 백그라운드) — 체크포인트 간격 축소용.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "lecture_beh_01"
down_revision: Union[str, None] = "lecture_tbl_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "lecture_watch_progress"


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    # lecture_tbl_01이 선행하므로 테이블은 항상 존재 — 없으면 여기서 시끄럽게 실패해야 한다
    cols = {c["name"] for c in insp.get_columns(_TABLE)}

    if "session_id" not in cols:
        op.add_column(_TABLE, sa.Column("session_id", sa.String(64), nullable=True))
    if "last_heartbeat_at" not in cols:
        op.add_column(_TABLE, sa.Column("last_heartbeat_at", sa.DateTime(), nullable=True))
    if "exempt_streak" not in cols:
        op.add_column(
            _TABLE,
            sa.Column("exempt_streak", sa.Integer(), nullable=False, server_default="0"),
        )
    if "suspicion" not in cols:
        op.add_column(
            _TABLE,
            sa.Column("suspicion", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    op.drop_column(_TABLE, "suspicion")
    op.drop_column(_TABLE, "exempt_streak")
    op.drop_column(_TABLE, "last_heartbeat_at")
    op.drop_column(_TABLE, "session_id")
