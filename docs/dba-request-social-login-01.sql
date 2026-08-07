-- ============================================================================
-- DBA 요청: social_accounts 테이블 생성 (소셜 로그인)
--
-- 요청자   : 김민용 (백엔드)
-- 대상 DB  : catchap_dev_db  (프로덕션)
-- 리비전   : social_login_01  (down_revision = course_thumbnail_01)
-- 작성일   : 2026-08-07
-- 관련 PR  : catchap-backend#3 / catchap-frontend#4
--
-- 왜 필요한가
--   학생이 카카오·네이버·구글 계정으로 가입·로그인한다. 이 테이블은 "어느 소셜 계정이
--   어느 학생인가"의 연결만 담는다. provider access token 은 저장하지 않는다 —
--   프로필을 한 번 읽는 데만 쓰고 버리므로 유출 표면도 갱신 부담도 없다.
--
-- 왜 백엔드 계정으로 못 하는가
--   앱 런타임 계정에 DDL 권한이 없다(SELECT/INSERT/UPDATE/DELETE 만).
--   `alembic upgrade head` 를 앱 계정으로 돌리면 CREATE command denied 로 실패한다.
--   앱이 실수로 스키마를 바꾸지 못하게 분리한 설계이므로 그대로 두는 것이 맞다.
--
-- ⚠️ 배포 순서 (중요)
--   이 DDL 이 **코드 배포보다 먼저** 적용되어야 한다.
--   테이블이 없는 상태로 코드를 올리면 소셜 로그인 콜백이 1146(Unknown table)로 죽고,
--   **탈퇴(익명화) 경로도 이 테이블을 지우려다 함께 실패한다**(privacy_service).
--   다만 그 외 기존 기능(강의·결제·문항)은 이 테이블을 읽지 않으므로 영향이 없다.
--   → 즉 "먼저 적용"이 안전하지만, 늦어도 서비스 전체가 멈추지는 않는다.
--
-- 영향 범위
--   신규 테이블 1개. 기존 테이블 변경 없음. 기존 데이터 마이그레이션 없음.
--   student_id 는 소프트 참조(FK 없이 인덱스만) — 신규 테이블 규약(collation 정합 회피).
--   되돌리기: 코드에서 provider 키를 비우면 기능이 꺼지고(버튼 미노출), 테이블은
--   남아도 무해하다. 실제 삭제가 필요하면 맨 아래 5) 참고.
-- ============================================================================

-- 1) 현재 상태 확인 (이미 있으면 2~3번은 건너뛴다)
SHOW TABLES LIKE 'social_accounts';

-- 2) 테이블 생성
CREATE TABLE social_accounts (
    id               CHAR(36)     NOT NULL,
    student_id       CHAR(36)     NOT NULL,
    provider         VARCHAR(20)  NOT NULL,           -- kakao | naver | google
    -- provider 식별자 길이는 provider 마다 다르다(카카오 숫자, 구글 sub 21자, 네이버 해시).
    -- 191 은 utf8mb4 + 복합 유니크 인덱스에서 안전한 상한이다.
    provider_user_id VARCHAR(191) NOT NULL,
    email            VARCHAR(255) NULL,               -- 연결 시점 사본(감사·CS용)
    email_verified   TINYINT(1)   NOT NULL DEFAULT 0, -- provider 가 소유를 확인해 줬는가
    last_login_at    DATETIME     NULL,
    created_at       DATETIME     NULL,
    updated_at       DATETIME     NULL,
    PRIMARY KEY (id),
    -- 같은 소셜 계정이 두 학생에 붙는 것을 DB 에서 막는다(계정 탈취 경로 차단).
    UNIQUE KEY uq_social_provider_user (provider, provider_user_id),
    -- 한 학생이 같은 provider 를 중복 연결하는 것도 막는다.
    UNIQUE KEY uq_social_student_provider (student_id, provider),
    KEY ix_social_student (student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3) 마이그레이션 마커 갱신
--    현재 값이 'course_thumbnail_01' 인지 먼저 확인한 뒤 실행한다.
--    (다르면 그 사이에 다른 마이그레이션이 들어온 것이므로 요청자에게 확인)
SELECT version_num FROM alembic_version;

UPDATE alembic_version
   SET version_num = 'social_login_01'
 WHERE version_num = 'course_thumbnail_01';

-- 4) 적용 확인
SHOW CREATE TABLE social_accounts;
SELECT version_num FROM alembic_version;
--    기대: 유니크 인덱스 2개(uq_social_provider_user, uq_social_student_provider)
--          + 일반 인덱스 1개(ix_social_student)
--          version_num = 'social_login_01'

-- 5) 되돌리기 (필요할 때만)
--    ⚠️ 연결 정보가 사라지므로, 이미 소셜로 가입한 학생은 로그인 수단을 잃는다.
--    비밀번호가 없는 소셜 전용 계정이면 계정에 다시 들어올 수 없다 — 실행 전 반드시
--    SELECT COUNT(*) FROM social_accounts; 로 사용자가 있는지 확인할 것.
-- DROP TABLE social_accounts;
-- UPDATE alembic_version SET version_num = 'course_thumbnail_01'
--  WHERE version_num = 'social_login_01';
