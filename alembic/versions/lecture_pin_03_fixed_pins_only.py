"""고정 핀만 — 구간 출제(window_sec) 제거

되감기(오답 상한)는 체크포인트(cp) 기준 cp-REWIND_SEC로 되감는데, 구간 출제는 cp가
[position, position+window] 안 무작위 지점이라 문항이 다루는 내용(position 근처)과
무관한 대목을 되감을 수 있다(position=200·window=300이면 cp=480 → 450~480을 다시
보지만 내용은 200 근처). 고정 핀만 남기면 cp == position_sec이라 이 어긋남이
구조적으로 사라진다. 구간의 목적이던 '매번 같은 초면 학생이 지점을 외운다' 방어는
의도적으로 버린다 — 학습 강화 설계에선 지점을 외워도 내용을 봐야 답한다.

데이터 접기:
- window_sec 드롭 자체가 접기다 — window>0 행은 position_sec(내용 시점) 고정 핀이
  된다. 실측(0717): 로컬(catchap)·클라우드(catchap_dev_db) 모두 window>0 행 0건이라
  실질 무손실.
- 예약 정합화: 살아 있는 예약(next_checkpoint_sec) 중 그 강의의 active 문항 시점과
  정확히 일치하지 않는 것만 해제한다 — 구간 안 무작위 지점에 잡힌 옛 예약은 새
  구조에서 낼 문항이 없어(발급 409 + cp 클램프) 학생이 영영 갇힌다. 핀 시점에 정확히
  걸린 예약은 유효하므로 두는데, 전면 해제(lecture_pin_02 방식)와 달리 지금 캡차
  대기 중인 학생의 체크포인트를 사면하지 않기 위해서다. 해제된 예약은 다음 하트비트가
  현재 핀 구성으로 다시 잡는다(lecture_service.advance의 재예약 경로 — 기존 규약).

downgrade는 컬럼 구조만 복원한다 — window 값은 복구 불가(실측 0건이라 잃는 정보 없음).

Revision ID: lecture_pin_03
Revises: lecture_pin_02
"""

import sqlalchemy as sa
from alembic import op

revision = "lecture_pin_03"
down_revision = "lecture_pin_02"
branch_labels = None
depends_on = None


def _cols(table: str) -> set[str]:
    bind = op.get_bind()
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    # 정합화는 드롭보다 먼저 — window_sec가 있든 없든 의미가 같아 재실행에도 안전하다.
    # (상관 서브쿼리는 별칭 없는 바깥 테이블 참조 — MySQL·SQLite 공통 문법)
    op.execute(
        "UPDATE lecture_watch_progress SET next_checkpoint_sec = NULL "
        "WHERE next_checkpoint_sec IS NOT NULL AND NOT EXISTS ("
        "  SELECT 1 FROM lecture_questions q"
        "  WHERE q.lecture_id = lecture_watch_progress.lecture_id"
        "    AND q.status = 'active'"
        "    AND q.position_sec = lecture_watch_progress.next_checkpoint_sec"
        ")"
    )
    if "window_sec" in _cols("lecture_questions"):
        op.drop_column("lecture_questions", "window_sec")


def downgrade() -> None:
    op.add_column(
        "lecture_questions",
        sa.Column("window_sec", sa.Integer(), nullable=False, server_default="0"),
    )
