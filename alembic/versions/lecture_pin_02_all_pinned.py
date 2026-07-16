"""전부 핀 구조 — 랜덤 간격(pinned=False 풀)·suspicion 제거

출제 시점이 전부 핀(문항의 position_sec+window_sec 구간)이 되면서:
- lecture_questions.pinned          드롭 — 구분 자체가 사라졌다(모든 행이 핀).
- lectures.check_min_sec/max_sec    드롭 — 무작위 확인 간격 설정. 쓸 곳이 없다.
- lecture_watch_progress.suspicion  드롭 — 간격 축소에만 쓰이던 일시 카운터.

데이터 접기:
- 공개(active)이면서 position_sec < 1인 기존 풀 행은 1초 핀으로 접는다. 핀은
  watched < start 판정이라 0초 핀은 영영 안 뜬다 — 접지 않으면 그 문항이 조용히
  죽고, 유일 문항이면 그 강의의 시청 검증이 통째로 꺼진다. draft(position 0)는
  '시점 미배치' 상태로 유효하므로 건드리지 않는다.
- 살아 있는 예약(next_checkpoint_sec)은 전부 해제한다 — 옛 무작위 간격으로 잡힌
  지점은 새 구조에서 어떤 핀 구간도 안 덮을 수 있고, 그 예약에 닿은 학생은
  게이트 409 + cp 클램프로 영영 갇힌다. 해제된 예약은 다음 하트비트가 현재 핀
  구성으로 다시 잡는다(lecture_service.advance의 재예약 경로 — 기존 규약).

downgrade는 컬럼 구조만 복원한다 — pinned 구분·간격 설정·suspicion 값은 복구
불가(각각 전부 False 기본값·강사 설정·일시 카운터라 잃는 정보가 없거나 사소하다).

Revision ID: lecture_pin_02
Revises: lecture_fail_01
"""

import sqlalchemy as sa
from alembic import op

revision = "lecture_pin_02"
down_revision = "lecture_fail_01"
branch_labels = None
depends_on = None


def _cols(table: str) -> set[str]:
    bind = op.get_bind()
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    # 접기는 드롭보다 먼저 — pinned 컬럼이 있든 없든 의미가 같아 재실행에도 안전하다
    op.execute(
        "UPDATE lecture_questions SET position_sec = 1 "
        "WHERE status = 'active' AND position_sec < 1"
    )
    op.execute(
        "UPDATE lecture_watch_progress SET next_checkpoint_sec = NULL "
        "WHERE next_checkpoint_sec IS NOT NULL"
    )
    if "pinned" in _cols("lecture_questions"):
        op.drop_column("lecture_questions", "pinned")
    lec_cols = _cols("lectures")
    if "check_min_sec" in lec_cols:
        op.drop_column("lectures", "check_min_sec")
    if "check_max_sec" in lec_cols:
        op.drop_column("lectures", "check_max_sec")
    if "suspicion" in _cols("lecture_watch_progress"):
        op.drop_column("lecture_watch_progress", "suspicion")


def downgrade() -> None:
    op.add_column(
        "lecture_questions",
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default="0"),
    )
    op.add_column(
        "lectures",
        sa.Column("check_min_sec", sa.Integer(), nullable=False, server_default="60"),
    )
    op.add_column(
        "lectures",
        sa.Column("check_max_sec", sa.Integer(), nullable=False, server_default="180"),
    )
    op.add_column(
        "lecture_watch_progress",
        sa.Column("suspicion", sa.Integer(), nullable=False, server_default="0"),
    )
