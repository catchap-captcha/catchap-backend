"""경제 무결성: 6종 UNIQUE 제약 + dedup + 멱등 보상/캡차 소비 테이블

race로 중복행이 생기던 테이블에 UNIQUE 제약을 추가한다. 제약 추가 전에 기존
중복행을 dedup(각 그룹에서 최소 id 1행만 유지 — 오래된 행이 원본일 가능성이 높고
결정적이라 안전) 한다. 또한 GET 보상 멱등 장부(daily_rewards)와 캡차 1회용 토큰
소비 장부(captcha_consumed_tokens)를 생성한다.

Revision ID: f4a5b6c7d8e9
Revises: e5f6a7b8c9d0
Create Date: 2026-07-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f4a5b6c7d8e9"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (table, unique_name, [cols]) — 6종 + 배지명/학생배지(자동지급 이중지급 차단)
_UNIQUES = [
    ("student_items", "uq_student_item", ["student_id", "item_id"]),
    ("daily_quiz_status", "uq_daily_quiz_student_date_subject", ["student_id", "quiz_date", "subject"]),
    ("parent_student_links", "uq_parent_student_link", ["parent_user_id", "student_id"]),
    ("user_settings", "uq_user_setting_subject", ["subject_type", "subject_id"]),
    ("student_progress", "uq_student_progress_subject", ["student_id", "subject"]),
    ("memberships", "uq_membership_user_org", ["user_id", "organization_id"]),
    ("badges", "uq_badge_name", ["name"]),
    ("student_badges", "uq_student_badge", ["student_id", "badge_id"]),
]


def _has_table(bind, table: str) -> bool:
    return sa.inspect(bind).has_table(table)


def _has_unique(bind, table: str, name: str) -> bool:
    insp = sa.inspect(bind)
    names = {uc.get("name") for uc in insp.get_unique_constraints(table)}
    names |= {ix["name"] for ix in insp.get_indexes(table) if ix.get("unique")}
    return name in names


def _dedup(bind, table: str, cols: list[str]) -> None:
    """각 그룹에서 최소 id 1행만 남기고 삭제 (MySQL self-join DELETE).

    NULL 컬럼(예: memberships.user_id 미클레임 코드)은 조인에서 매칭되지 않아
    보존된다(NULL != NULL) — 선발급 코드 다수 허용.
    """
    on = " AND ".join(f"t2.`{c}` = t.`{c}`" for c in cols)
    op.execute(
        sa.text(
            f"DELETE t FROM `{table}` t JOIN `{table}` t2 ON {on} AND t2.id < t.id"
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    is_mysql = bind.dialect.name == "mysql"

    if is_mysql:
        for table, name, cols in _UNIQUES:
            if not _has_table(bind, table):
                continue
            _dedup(bind, table, cols)
            if not _has_unique(bind, table, name):
                op.create_unique_constraint(name, table, cols)

    # 멱등 보상 장부 (하루 1회 지급의 이중지급 차단)
    if not _has_table(bind, "daily_rewards"):
        op.create_table(
            "daily_rewards",
            sa.Column("id", sa.CHAR(36), primary_key=True),
            sa.Column("student_id", sa.CHAR(36), nullable=False),
            sa.Column("kind", sa.String(30), nullable=False),
            sa.Column("reward_date", sa.Date(), nullable=False),
            sa.Column("amount", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
            sa.UniqueConstraint("student_id", "kind", "reward_date", name="uq_daily_reward"),
        )
        op.create_index("ix_daily_rewards_student_id", "daily_rewards", ["student_id"])
        op.create_index("ix_daily_rewards_kind", "daily_rewards", ["kind"])
        op.create_index("ix_daily_rewards_reward_date", "daily_rewards", ["reward_date"])

    # 캡차 1회용 토큰 소비 장부 (challenge nonce · verdict jti 리플레이 차단)
    if not _has_table(bind, "captcha_consumed_tokens"):
        op.create_table(
            "captcha_consumed_tokens",
            sa.Column("id", sa.CHAR(36), primary_key=True),
            sa.Column("kind", sa.String(20), nullable=False),
            sa.Column("token_id", sa.String(64), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
            sa.UniqueConstraint("kind", "token_id", name="uq_captcha_consumed"),
        )
        op.create_index("ix_captcha_consumed_kind", "captcha_consumed_tokens", ["kind"])
        op.create_index("ix_captcha_consumed_token_id", "captcha_consumed_tokens", ["token_id"])


def downgrade() -> None:
    bind = op.get_bind()
    is_mysql = bind.dialect.name == "mysql"

    if _has_table(bind, "captcha_consumed_tokens"):
        op.drop_table("captcha_consumed_tokens")
    if _has_table(bind, "daily_rewards"):
        op.drop_table("daily_rewards")

    if is_mysql:
        for table, name, _cols in _UNIQUES:
            if _has_table(bind, table) and _has_unique(bind, table, name):
                op.drop_constraint(name, table, type_="unique")
