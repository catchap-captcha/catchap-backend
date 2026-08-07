-- ============================================================================
-- DBA 요청: 0807 배포분 남은 DDL 2건 + head 단일화 stamp
--
-- 요청자   : 김민용 (백엔드)
-- 대상 DB  : catchap_dev_db  (프로덕션)
-- 작성일   : 2026-08-07
-- 리비전   : captcha_purge_01(#7) / instructor_temp_pw_expiry_01(#10) → merge_heads_0807
--
-- 배경
--   같은 날 세 PR이 각자 리비전을 얹어 head가 셋이 됐다. 그중 social_login_02(#9)는
--   이미 적용됐고(2026-08-07 08:44 UTC 실측: social_accounts.user_id 존재, 연결 3건,
--   alembic_version = 'social_login_02'), 나머지 둘이 남았다.
--
--     [적용됨] social_login_02              — social_accounts.user_id           (#9 김민용)
--     [남음]   captcha_purge_01             — ix_captcha_consumed_expires 인덱스 (#7 김태형)
--     [남음]   instructor_temp_pw_expiry_01 — users.password_reset_expires_at   (#10 지영)
--
--   merge_heads_0807 은 스키마를 바꾸지 않는 합류 리비전이다. 세 갈래를 하나로 묶어
--   앞으로 `alembic upgrade head` 가 "Multiple head revisions" 로 멈추지 않게 하고,
--   stamp 할 값을 하나로 정한다.
--
-- ★★ 가장 중요 — 2)를 코드 배포보다 먼저 해야 한다
--   users.password_reset_expires_at 는 User 모델에 매핑돼 있다(app/models/user.py:29).
--   컬럼 없이 main 코드를 올리면 users 를 읽는 모든 쿼리가 1054(Unknown column)로 죽는다.
--   = 소셜 로그인만이 아니라 **전체 로그인 장애**다. 지금 떠 있는 컨테이너는 #10 이전
--   코드라 아직 안전하다(08:44 UTC 실측). 이 SQL이 배포보다 먼저 들어가야 한다.
--
--   1)(인덱스)은 additive라 순서 무관하고 성능 목적이다. 급하지 않다.
--
-- 영향 범위
--   인덱스 1개 + NULL 허용 컬럼 1개. 데이터 이관·삭제 없음. 기존 행은 그대로 산다.
-- ============================================================================

-- 0) 적용 전 확인
SELECT version_num FROM alembic_version;                    -- 기대: social_login_02 한 행
SHOW INDEX FROM captcha_consumed_tokens WHERE Key_name = 'ix_captcha_consumed_expires';
SHOW COLUMNS FROM users LIKE 'password_reset_expires_at';
--    위 둘이 비어 있으면 아래 1)·2)를 그대로 실행한다. 이미 있으면 그 항목만 건너뛴다.

-- 1) captcha_purge_01 — 만료된 1회용 토큰 청소용 인덱스 (#7)
--    이미 있으면 아무것도 하지 않는다(멱등).
SET @exists := (SELECT COUNT(*) FROM information_schema.statistics
                 WHERE table_schema = DATABASE()
                   AND table_name = 'captcha_consumed_tokens'
                   AND index_name = 'ix_captcha_consumed_expires');
SET @sql := IF(@exists = 0,
    'CREATE INDEX ix_captcha_consumed_expires ON captcha_consumed_tokens (expires_at)',
    'SELECT 1');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 2) instructor_temp_pw_expiry_01 — 임시비밀번호 72h 만료 (#10)  ★배포보다 먼저
SET @exists := (SELECT COUNT(*) FROM information_schema.columns
                 WHERE table_schema = DATABASE()
                   AND table_name = 'users'
                   AND column_name = 'password_reset_expires_at');
SET @sql := IF(@exists = 0,
    'ALTER TABLE users ADD COLUMN password_reset_expires_at DATETIME NULL',
    'SELECT 1');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 3) 단일 stamp
--    alembic_version 이 한 행('social_login_02')이면 아래 UPDATE 하나로 끝난다.
UPDATE alembic_version
   SET version_num = 'merge_heads_0807'
 WHERE version_num IN ('social_login_02', 'captcha_purge_01', 'instructor_temp_pw_expiry_01');

--    만약 0)에서 행이 둘 이상으로 갈라져 있었다면, 위 UPDATE가 유니크 충돌을 낼 수 있다.
--    그때는 아래처럼 비우고 한 행만 넣는다(테이블에 다른 컬럼은 없다).
-- DELETE FROM alembic_version;
-- INSERT INTO alembic_version (version_num) VALUES ('merge_heads_0807');

-- 4) 적용 확인
SELECT version_num FROM alembic_version;                    -- 기대: merge_heads_0807 한 행
SHOW INDEX FROM captcha_consumed_tokens WHERE Key_name = 'ix_captcha_consumed_expires';
SHOW COLUMNS FROM users LIKE 'password_reset_expires_at';   -- DATETIME, NULL 허용
SELECT COUNT(*) AS 소셜연결수 FROM social_accounts;          -- 적용 전과 같아야 한다(실측 3)

-- 5) 되돌리기 (필요할 때만)
--    컬럼은 nullable이라 구 코드에서도 무해하다 — 배포를 롤백해도 컬럼은 두는 편이 낫다.
-- DROP INDEX ix_captcha_consumed_expires ON captcha_consumed_tokens;
-- ALTER TABLE users DROP COLUMN password_reset_expires_at;
-- UPDATE alembic_version SET version_num = 'social_login_02'
--  WHERE version_num = 'merge_heads_0807';
