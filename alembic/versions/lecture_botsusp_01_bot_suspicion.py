"""lecture_watch_progress — 봇 의심 누적 컬럼(캡차 승급 트리거)

Revision ID: lecture_botsusp_01
Revises: course_payment_pg_02
Create Date: 2026-07-29

시청 중 이상행동을 하트비트마다 누적해, 임계를 넘으면 메인 캡차(드래그·행동 AI)를
띄우기 위한 컬럼. 신호는 새로 만들지 않았고 이미 계산되던 세 값을 쓴다:
position 자기신고의 wall-clock 초과, 동시접속 충돌, 체크포인트 연속 오답 상한.

0717(lecture_pin_02)에 드롭된 `suspicion` 과 이름을 달리한 이유:
그건 체크포인트 '간격을 좁히는' 감시 장치였고 고정 핀 전환으로 쓸 곳이 없어졌다.
이건 별개 캡차를 띄우는 트리거이며 핀 예약에는 손대지 않는다. 같은 이름을 재사용하면
드롭 이력과 섞여 어느 쪽 의미인지 읽는 사람이 알 수 없게 된다.

되돌리기: BOT_ESCALATION_MODE=off 로 코드 경로가 전부 죽는다. 컬럼이 남아도 무해하다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "lecture_botsusp_01"
down_revision: Union[str, None] = "course_payment_pg_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "lecture_watch_progress"
_COL = "bot_suspicion"


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    # 선행 리비전이 테이블을 만들어 두었으므로 없으면 시끄럽게 실패하는 것이 맞다.
    cols = {c["name"] for c in insp.get_columns(_TABLE)}
    if _COL in cols:
        return  # 재실행 안전
    op.add_column(
        _TABLE,
        # server_default 를 주는 이유: 기존 행에 NULL 이 남으면 int() 캐스팅 지점마다
        # None 처리를 흘려야 한다. 0 으로 채워 들어오는 편이 읽기 쉽다.
        sa.Column(_COL, sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns(_TABLE)}
    if _COL in cols:
        op.drop_column(_TABLE, _COL)
