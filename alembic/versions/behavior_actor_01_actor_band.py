"""행동데이터 행위자 연령대 태깅 — actor_band + 기존 행 전량 'adult' 백필

Revision ID: behavior_actor_01
Revises: signup_age_01
Create Date: 2026-07-17

- behavior_summaries.actor_band VARCHAR(10) NULL: adult(만 14세 이상)|minor(만 14세
  미만·아동)|NULL(미상). 이후 적재는 쓰기 경로(record_behavior_event)가 값을 채운다.
- 백필: 마이그레이션 시점에 존재하는 모든 행 → 'adult'. 근거 = 사용자 확정(2026-07-17)
  "지금 쌓여 있는 행동데이터는 성인(팀원)이 만든 것이라 보존·사용한다" — 이후 아동
  테스트계정 파기 때 이 태그가 성인 생성분을 지키는 판별 축이 된다.
  (server_default를 쓰지 않는 이유: 미래의 미상 행은 'adult'가 아니라 NULL이어야 한다.)
멱등: 컬럼이 이미 있으면 추가·백필 모두 건너뛴다(백필 재실행 방지).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "behavior_actor_01"
down_revision: Union[str, None] = "signup_age_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    cols = {c["name"] for c in insp.get_columns("behavior_summaries")}
    if "actor_band" in cols:
        return  # 이미 적용 — 백필 중복 실행 방지
    op.add_column("behavior_summaries", sa.Column("actor_band", sa.String(length=10), nullable=True))
    # 기존 축적분 = 성인 생성 (사용자 확정 2026-07-17). 새 컬럼 직후라 전 행이 NULL — 전량 지정.
    bind.execute(sa.text("UPDATE behavior_summaries SET actor_band = 'adult'"))


def downgrade() -> None:
    op.drop_column("behavior_summaries", "actor_band")
