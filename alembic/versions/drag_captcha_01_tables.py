"""drag captcha runtime tables (ms 캡차 자체 이식)

우리 메인 캡차(사람확인)를 ms의 '다중 객체 드래그' 캡차로 교체하기 위한 런타임 테이블.
외부 ms 서비스에 의존하지 않고 우리 백엔드가 챌린지 발급·검증·토큰을 자체 처리한다.
문제 저작(라벨링/생성)은 이식 범위 밖 — 승인된 문제 데이터만 우리 DB로 들여온다.
스키마는 ms drag-captcha/app/db.py의 SCHEMA를 그대로 옮긴 것(captcha_users는 우리
계정 시스템이 대신하므로 제외). DRAG_CAPTCHA_ENABLED 플래그로 실제 사용 여부를 제어.

Revision ID: drag_captcha_01
Revises: lecture_thumbnail_01
"""
from alembic import op

revision = "drag_captcha_01"
down_revision = "lecture_thumbnail_01"
branch_labels = None
depends_on = None


_TABLES = [
    """CREATE TABLE IF NOT EXISTS captcha_questions (
      id VARCHAR(64) PRIMARY KEY, type VARCHAR(32) NOT NULL,
      instruction_ko VARCHAR(500) NOT NULL, instruction_en VARCHAR(500) NULL,
      source VARCHAR(64) NOT NULL, source_question_id VARCHAR(128) NULL,
      image_path VARCHAR(500) NOT NULL, image_width INT UNSIGNED NOT NULL,
      image_height INT UNSIGNED NOT NULL, difficulty TINYINT UNSIGNED NOT NULL DEFAULT 2,
      status VARCHAR(24) NOT NULL DEFAULT 'draft', review_status VARCHAR(24) NOT NULL DEFAULT 'pending',
      reviewer VARCHAR(128) NULL, reviewed_at DATETIME(6) NULL, created_at DATETIME(6) NOT NULL,
      INDEX idx_question_status(status, review_status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS captcha_objects (
      id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, question_id VARCHAR(64) NOT NULL,
      object_key VARCHAR(128) NOT NULL, label VARCHAR(128) NOT NULL,
      bbox_x DOUBLE NOT NULL, bbox_y DOUBLE NOT NULL, bbox_width DOUBLE NOT NULL, bbox_height DOUBLE NOT NULL,
      role VARCHAR(16) NOT NULL, piece_path VARCHAR(500) NULL,
      UNIQUE KEY uq_question_object(question_id, object_key), INDEX idx_object_question(question_id),
      CONSTRAINT fk_object_question FOREIGN KEY(question_id) REFERENCES captcha_questions(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS captcha_challenges_v2 (
      id CHAR(36) PRIMARY KEY, question_id VARCHAR(64) NOT NULL, session_id VARCHAR(128) NOT NULL,
      purpose VARCHAR(32) NOT NULL, expires_at DATETIME(6) NOT NULL,
      attempt_count TINYINT UNSIGNED NOT NULL DEFAULT 0, status VARCHAR(16) NOT NULL DEFAULT 'issued',
      created_at DATETIME(6) NOT NULL, verified_at DATETIME(6) NULL,
      client_ip_hash CHAR(64) NOT NULL, INDEX idx_challenge_expiry(expires_at),
      INDEX idx_challenge_rate(client_ip_hash, created_at),
      CONSTRAINT fk_challenge_question FOREIGN KEY(question_id) REFERENCES captcha_questions(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS captcha_challenge_objects (
      challenge_id CHAR(36) NOT NULL, object_id BIGINT UNSIGNED NOT NULL,
      temporary_object_id VARCHAR(64) NOT NULL,
      PRIMARY KEY(challenge_id, temporary_object_id), UNIQUE KEY uq_challenge_object(challenge_id, object_id),
      CONSTRAINT fk_map_challenge FOREIGN KEY(challenge_id) REFERENCES captcha_challenges_v2(id) ON DELETE CASCADE,
      CONSTRAINT fk_map_object FOREIGN KEY(object_id) REFERENCES captcha_objects(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS captcha_attempts (
      id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, challenge_id CHAR(36) NOT NULL,
      selected_object_ids JSON NOT NULL, is_correct BOOLEAN NOT NULL,
      failure_reason VARCHAR(64) NULL, duration_ms INT UNSIGNED NOT NULL,
      behavior_summary JSON NULL, raw_event_path VARCHAR(500) NULL, created_at DATETIME(6) NOT NULL,
      INDEX idx_attempt_challenge(challenge_id),
      CONSTRAINT fk_attempt_challenge FOREIGN KEY(challenge_id) REFERENCES captcha_challenges_v2(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS captcha_tokens (
      id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, challenge_id CHAR(36) NOT NULL,
      token_hash CHAR(64) NOT NULL UNIQUE, purpose VARCHAR(32) NOT NULL,
      session_id VARCHAR(128) NOT NULL, expires_at DATETIME(6) NOT NULL, consumed_at DATETIME(6) NULL,
      created_at DATETIME(6) NOT NULL,
      CONSTRAINT fk_token_challenge FOREIGN KEY(challenge_id) REFERENCES captcha_challenges_v2(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
]

# 자식→부모 순서로 DROP (FK 제약)
_DROP_ORDER = [
    "captcha_tokens",
    "captcha_attempts",
    "captcha_challenge_objects",
    "captcha_challenges_v2",
    "captcha_objects",
    "captcha_questions",
]


def upgrade() -> None:
    for stmt in _TABLES:
        op.execute(stmt)


def downgrade() -> None:
    for table in _DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table}")
