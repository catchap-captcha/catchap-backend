"""motion_samples: 포인터 움직임 요약 — 봇 판별용. 좌표는 저장하지 않는다.

왜: 지금 궤적 분석은 캡차가 떠 있는 몇 초 동안만 돈다. 강의는 40분을 머무는데 그동안
아무것도 안 보고 있고, 거기가 시청 시뮬레이션 봇이 노는 자리다.

좌표를 다 받으면 40분에 1.8MB · 하루 수백만 행이다. 더 큰 이유는 따로 있다 —
마우스 궤적은 그것만으로 사람이 구분된다(2026-08-12 실측: 같은 사람 4명 전원 식별,
다른 사람 오인 0.040%). 그래서 브라우저가 세고 숫자 일곱 개만 보낸다.

한 줄 = 강의 하트비트 한 구간(10초) 또는 로그인·시험 한 번.

용량: 학생 100명이 하루 40분씩 보면 24,000행. 한 행이 100바이트 안팎이라 하루 2.4MB.
90일이면 216MB — 보관 기간을 정할 때 이 크기를 기준으로 본다.

인덱스를 셋 두는 이유 — 분포를 볼 때 쓰는 질의가 정해져 있다.
  surface     "강의 화면에서 정상 사용자는 어떤 숫자인가"
  subject_id  "이 학생이 계속 이상한가"
  created_at  "최근 것만" · 보관 기간 청소

Revision ID: motion_samples_01
Revises: merge_heads_0807
"""
import sqlalchemy as sa
from alembic import op

revision = "motion_samples_01"
down_revision = "merge_heads_0807"
branch_labels = None
depends_on = None

_TABLE = "motion_samples"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.CHAR(36), primary_key=True),
        # 어느 화면인가 — lecture · login · exam
        sa.Column("surface", sa.String(16), nullable=False),
        # 누구인가. 로그인은 인증 **전**이라 비어 있다 — 그 화면은 분포만 본다.
        sa.Column("subject_id", sa.CHAR(36), nullable=True),
        # 무엇에 대한 것인가 — 강의 id · 시험 회차 id. 화면마다 뜻이 다르다.
        sa.Column("context_id", sa.String(64), nullable=True),
        # 표본 수. 0 이면 그 구간에 움직임이 없었다는 뜻이고 그 자체는 판단하지 않는다 —
        # 강의를 집중해서 보는 사람이 정확히 그렇게 행동한다.
        sa.Column("n", sa.Integer, nullable=False, server_default="0"),
        sa.Column("dist", sa.Float, nullable=False, server_default="0"),
        sa.Column("span", sa.Float, nullable=False, server_default="0"),
        sa.Column("turns", sa.Integer, nullable=False, server_default="0"),
        sa.Column("micro", sa.Float, nullable=False, server_default="0"),
        sa.Column("pauses", sa.Integer, nullable=False, server_default="0"),
        sa.Column("gaps", sa.Float, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime, nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP"),
                  server_onupdate=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_motion_surface", _TABLE, ["surface"])
    op.create_index("ix_motion_subject", _TABLE, ["subject_id"])
    op.create_index("ix_motion_created", _TABLE, ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_motion_created", table_name=_TABLE)
    op.drop_index("ix_motion_subject", table_name=_TABLE)
    op.drop_index("ix_motion_surface", table_name=_TABLE)
    op.drop_table(_TABLE)
