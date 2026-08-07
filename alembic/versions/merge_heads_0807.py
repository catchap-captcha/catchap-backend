"""세 갈래 head 합류 — captcha_purge_01 · instructor_temp_pw_expiry_01 · social_login_02

Revision ID: merge_heads_0807
Revises: captcha_purge_01, instructor_temp_pw_expiry_01, social_login_02
Create Date: 2026-08-07

같은 날 세 PR이 각자 course_thumbnail_01/social_login_01 위에 리비전을 얹으면서 head가
셋이 됐다(#7 캡차 인덱스, #10 임시비번 만료 컬럼, #9 콘솔 소셜 연결).

head가 여럿이면 `alembic upgrade head`가 "Multiple head revisions" 로 멈추고,
alembic_version 테이블도 head 수만큼 행을 갖는다. 운영에 stamp 할 값이 하나로 정해지지
않는다는 뜻이라, 배포 때마다 "무엇을 stamp 하느냐"를 사람이 판단해야 한다.

이 리비전은 스키마를 **바꾸지 않는다**. 세 갈래를 하나로 합쳐 head를 다시 1개로 만드는
표지판일 뿐이다. 그래서 upgrade/downgrade가 비어 있는 게 정상이다.

DDL 적용 후 stamp 는 이 값 하나로 끝난다:
    UPDATE alembic_version SET version_num = 'merge_heads_0807';
(단, alembic_version 이 한 행일 때. 세 행으로 갈라져 있으면 두 행을 지우고 하나를 갱신한다 —
 docs/dba-request-merge-heads-0807.sql 참고)
"""
from typing import Sequence, Union

revision: str = "merge_heads_0807"
down_revision: Union[str, Sequence[str], None] = (
    "captcha_purge_01",
    "instructor_temp_pw_expiry_01",
    "social_login_02",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """스키마 변경 없음 — 갈래를 합치는 표지판."""


def downgrade() -> None:
    """되돌리면 head가 다시 셋으로 갈라진다. 스키마는 그대로."""
