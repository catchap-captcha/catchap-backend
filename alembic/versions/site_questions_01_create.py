"""site_questions: 고객사가 직접 넣는 캡차 문항.

왜: 지금은 문제·정답·채점이 전부 우리 서버에 있고 고객사는 고를 수 없다. 그건 안전하지만,
"우리 회사 로고 색은?" 처럼 그 사이트를 아는 사람만 아는 문제를 섞고 싶다는 요구가 있다.
자기 문항이 섞이면 남의 봇이 우리 기본 문제만 학습해 온 경우에도 걸린다.

★섞어서 낸다 — 자기 문항만 내면 몇 개 안 되는 문제가 돌고 돌아 봇이 외운다.
  문항이 없거나 전부 내려간 사이트는 기본 문제만 나온다(자기 문항 때문에 캡차가 멈추지 않게).

정답(answer)은 이 표에만 있고 화면으로 내려가지 않는다 — 기존 캡차와 같은 규약.

Revision ID: site_questions_01
Revises: motion_samples_01
"""
import sqlalchemy as sa
from alembic import op

revision = "site_questions_01"
down_revision = "motion_samples_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "site_questions",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("site_id", sa.CHAR(36), sa.ForeignKey("sites.id"), nullable=False),
        # 권한 확인용 비정규화 — api_keys 와 같은 방식(사이트를 한 번 더 타지 않게)
        sa.Column("organization_id", sa.CHAR(36), nullable=False),
        sa.Column("prompt", sa.String(300), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("answer", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    # 출제할 때 쓰는 질의: "이 사이트의 살아 있는 문항" — 두 칸을 같이 본다
    op.create_index("ix_site_questions_site_status", "site_questions", ["site_id", "status"])
    op.create_index("ix_site_questions_org", "site_questions", ["organization_id"])


def downgrade() -> None:
    op.drop_index("ix_site_questions_org", table_name="site_questions")
    op.drop_index("ix_site_questions_site_status", table_name="site_questions")
    op.drop_table("site_questions")
