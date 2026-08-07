-- ============================================================================
-- DBA 요청: social_accounts 에 user_id 추가 (콘솔 계정 소셜 연결)
--
-- 요청자   : 김민용 (백엔드)
-- 대상 DB  : catchap_dev_db  (프로덕션)
-- 리비전   : social_login_02  (down_revision = social_login_01)
-- 작성일   : 2026-08-07
--
-- 왜 필요한가
--   콘솔 계정(운영자·강사)은 지금 소셜 로그인이 막혀 있다. 고권한 계정을 외부 IdP
--   공격면에 두지 않으려는 방침이다. 이 변경은 그 방침을 **유지하면서** 예외를 연다 —
--   본인이 비밀번호로 로그인한 뒤 **명시적으로 연결한 경우에만** 소셜 로그인을 허용한다.
--   '이메일이 같으니 자동으로 붙인다'는 하지 않는다(계정 탈취 경로).
--   → 그래서 user_id 가 채워진 행 자체가 '본인이 동의했다'는 증거다.
--
-- 왜 백엔드 계정으로 못 하는가
--   ALTER 는 DDL 이고 앱 런타임 계정은 DML 전용이다(social_login_01 과 같은 이유).
--
-- ⚠️ 배포 순서
--   이 DDL 이 **코드 배포보다 먼저**. 컬럼 없이 새 코드를 올리면 ORM 이 user_id 를
--   SELECT 목록에 넣으므로 소셜 로그인 전체가 1054(Unknown column)로 실패한다.
--   ★이미 학생 연결 행이 있으므로(운영 실측 2건, 2026-08-07) 그 사용자들이 즉시 영향을
--   받는다. 반대로 DDL 만 먼저 적용하고 코드가 늦는 것은 무해하다(구 코드는 이 컬럼을
--   모른다). 그러니 **DDL → 코드** 순서를 지킬 것.
--
-- 영향 범위
--   컬럼 1개 추가 + 인덱스 2개 + student_id 를 NULL 허용으로 완화.
--   기존 행은 그대로 산다(user_id = NULL). 데이터 이관 없음.
--   되돌리기: 맨 아래 5) 참고. 콘솔 연결 행만 사라지고 학생 연결은 영향 없다.
-- ============================================================================

-- 1) 현재 상태 확인 (user_id 가 이미 있으면 2~4번은 건너뛴다)
SHOW COLUMNS FROM social_accounts LIKE 'user_id';
SELECT COUNT(*) AS 기존연결수 FROM social_accounts;

-- 2) 컬럼 추가
ALTER TABLE social_accounts
    ADD COLUMN user_id CHAR(36) NULL AFTER student_id;

-- 3) 인덱스
--    MySQL 은 NULL 을 유일성 검사에서 제외한다 → 학생 행(user_id=NULL)이 아무리 많아도
--    서로 충돌하지 않고, 콘솔 계정만 (user_id, provider) 중복이 막힌다.
ALTER TABLE social_accounts
    ADD UNIQUE KEY uq_social_user_provider (user_id, provider),
    ADD KEY ix_social_user (user_id);

-- 4) student_id 를 NULL 허용으로 완화 (콘솔 연결 행은 이 칸이 빈다)
ALTER TABLE social_accounts
    MODIFY student_id CHAR(36) NULL;

-- 5) 마이그레이션 마커 갱신
--    현재 값이 'social_login_01' 인지 먼저 확인한 뒤 실행한다.
SELECT version_num FROM alembic_version;

UPDATE alembic_version
   SET version_num = 'social_login_02'
 WHERE version_num = 'social_login_01';

-- 6) 적용 확인
SHOW CREATE TABLE social_accounts;
SELECT version_num FROM alembic_version;
--    기대: user_id CHAR(36) NULL / student_id NULL 허용
--          유니크 3개(provider+provider_user_id, student_id+provider, user_id+provider)
--          version_num = 'social_login_02'

-- 7) 되돌리기 (필요할 때만)
--    콘솔 계정 연결만 사라진다. 학생 연결(student_id 채워진 행)은 영향 없다.
--    ⚠️ student_id 를 NOT NULL 로 되돌리려면 user_id 행을 먼저 지워야 한다.
-- DELETE FROM social_accounts WHERE user_id IS NOT NULL;
-- ALTER TABLE social_accounts DROP INDEX uq_social_user_provider, DROP INDEX ix_social_user;
-- ALTER TABLE social_accounts DROP COLUMN user_id;
-- ALTER TABLE social_accounts MODIFY student_id CHAR(36) NOT NULL;
-- UPDATE alembic_version SET version_num = 'social_login_01'
--  WHERE version_num = 'social_login_02';
