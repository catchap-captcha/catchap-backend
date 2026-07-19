"""운영자 AI 모델 선택 — ai_model_configs(생성/검증 슬롯·On/Off·토큰/추정비용)

Revision ID: ai_model_cfg_01
Revises: course_exam_01
Create Date: 2026-07-19

#26 운영자 AI 모델 선택. 실제 LLM 호출(문항 생성·자기검증)에 쓰는 모델의 런타임 설정.
표시용 카탈로그 model_versions와 별개. 멱등: 존재 검사 후 건너뛴다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "ai_model_cfg_01"
down_revision: Union[str, None] = "course_exam_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "ai_model_configs" in insp.get_table_names():
        return
    op.create_table(
        "ai_model_configs",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("provider", sa.String(60), nullable=False),
        sa.Column("model_id", sa.String(120), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("cost_in_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("cost_out_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("tokens_in", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    # 슬롯 배정(생성/검증)은 system_settings의 포인터로 둔다 — 같은 모델을 두 슬롯에 함께
    # 쓸 수 있게(컬럼으로 두면 한 모델이 한 슬롯만 가짐). 그래서 slot 컬럼/인덱스는 없다.


def downgrade() -> None:
    op.drop_table("ai_model_configs")
