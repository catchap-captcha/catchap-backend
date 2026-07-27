"""drag captcha: 세션 기준 레이트리밋 인덱스

왜: 챌린지를 발급할 때마다 _request_pattern()이 세 가지를 센다.
  (1) client_ip_hash 기준 1분  → idx_challenge_rate(client_ip_hash, created_at) 로 range 스캔
  (2) session_id 기준 10분     → **인덱스 없음 → 풀스캔**
  (3) 세션의 최근 실패 횟수     → captcha_attempts 를 드라이빙 테이블로 **풀스캔**

프로덕션 EXPLAIN 실측(2026-07-27): (1)은 type=range/key=idx_challenge_rate 인데
(2)와 (3)은 type=ALL/key=NULL 이었다. 지금은 행이 적어 티가 안 나지만, 이 테이블은
챌린지가 발급될 때마다 커지므로 캡차를 쓸수록 발급이 느려지는 구조다.

session_id 를 선두 컬럼으로 둔 복합 인덱스 하나면 (2)는 곧바로 range 로 바뀌고,
(3)도 옵티마이저가 captcha_challenges_v2 를 먼저 타고 captcha_attempts 는
idx_attempt_challenge(challenge_id) 로 접근하도록 계획을 바꿀 수 있다.

created_at 을 두 번째 컬럼에 붙이는 이유는 (2)가 session_id 등호 + created_at 범위라서
인덱스만으로 조건이 끝나기 때문이다.

Revision ID: drag_captcha_02
Revises: drag_captcha_01
"""
from alembic import op

revision = "drag_captcha_02"
down_revision = "drag_captcha_01"
branch_labels = None
depends_on = None

_INDEX = "idx_challenge_session"
_TABLE = "captcha_challenges_v2"


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
            'CREATE INDEX {_INDEX} ON {_TABLE} (session_id, created_at)',
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
