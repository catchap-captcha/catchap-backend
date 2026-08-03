"""captcha_consumed_tokens: 만료 청소용 expires_at 인덱스

왜: 이 표는 1회용 캡차 토큰의 소비 기록이다. 토큰 수명이 몇 분(메인 캡차 180초)인데
**지우는 코드가 없어 계속 쌓이고 있었다.**

  2026-08-03 실측: 29,001행 · 14MB · 2026-07-11 ~ 08-02 (하루 약 1,400행)
                   그중 28,998행이 이미 만료 — 즉 99.99% 가 죽은 데이터다.

만료된 행은 리플레이 차단에도 더는 필요 없다. 토큰 자체가 `_unsign()` 에서
`exp < now` 로 먼저 거절되므로, 그 토큰으로 `_consume()` 까지 오지 못한다.

인덱스를 같이 넣는 이유 — 청소는 `expires_at < cutoff` 범위로 지운다.
기존 인덱스는 PRIMARY · uq(kind,token_id) · ix(kind) · ix(token_id) 뿐이라
**expires_at 으로 지우면 풀스캔 + 정렬**이다. 지금은 3만 행이라 티가 안 나지만,
캡차를 쓸수록 커지는 표라서 **쓸수록 청소가 느려지는** 구조가 된다.
(같은 이유로 drag_captcha 도 idx_challenge_expiry 를 두고 ORDER BY + LIMIT 으로 지운다)

Revision ID: captcha_purge_01
Revises: lecture_botsusp_01
"""
from alembic import op

revision = "captcha_purge_01"
down_revision = "lecture_botsusp_01"
branch_labels = None
depends_on = None

_INDEX = "ix_captcha_consumed_expires"
_TABLE = "captcha_consumed_tokens"


def upgrade() -> None:
    # 멱등: 이미 있으면 건너뛴다(수동 적용분과 충돌하지 않게).
    op.execute(
        f"""
        SET @exists := (SELECT COUNT(*) FROM information_schema.statistics
                        WHERE table_schema = DATABASE() AND table_name = '{_TABLE}'
                          AND index_name = '{_INDEX}');
        """
    )
    op.execute(
        f"""
        SET @sql := IF(@exists = 0,
            'CREATE INDEX {_INDEX} ON {_TABLE} (expires_at)',
            'SELECT 1');
        """
    )
    op.execute("PREPARE stmt FROM @sql")
    op.execute("EXECUTE stmt")
    op.execute("DEALLOCATE PREPARE stmt")


def downgrade() -> None:
    op.execute(
        f"""
        SET @exists := (SELECT COUNT(*) FROM information_schema.statistics
                        WHERE table_schema = DATABASE() AND table_name = '{_TABLE}'
                          AND index_name = '{_INDEX}');
        """
    )
    op.execute(
        f"""
        SET @sql := IF(@exists > 0, 'DROP INDEX {_INDEX} ON {_TABLE}', 'SELECT 1');
        """
    )
    op.execute("PREPARE stmt FROM @sql")
    op.execute("EXECUTE stmt")
    op.execute("DEALLOCATE PREPARE stmt")
