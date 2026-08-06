-- ============================================================================
-- CatChap 운영 DB 스키마 변경 요청 — DBA 실행용
--
--   대상 DB   : catchap_dev_db  (10.0.1.168:3306)   ※ 운영 DB(앱 접속 대상)
--   마이그레이션: course_thumbnail_01  "코스 자체 대표 썸네일(강의 없이도 커버)"
--   현재 상태 : alembic_version = lecture_report_01
--   변경 후   : alembic_version = course_thumbnail_01
--
-- 왜 DBA가 실행해야 하나
--   앱 계정 catchap_backend 의 권한은 SELECT/INSERT/UPDATE/DELETE 뿐이라 ALTER TABLE 을
--   실행할 수 없다. 실제로 배포 중 아래 오류로 막혔다:
--     ERROR 1142: ALTER command denied to user 'catchap_backend'@'10.0.1.73' for table 'courses'
--
-- 왜 지금 필요한가
--   새 백엔드 코드의 ORM(Course 모델)이 courses 를 조회할 때 thumbnail_ext 를 SELECT 목록에
--   포함한다. 이 컬럼을 먼저 추가하지 않고 새 코드를 배포하면 코스를 읽는 모든 요청이
--   MySQL 1054(Unknown column)로 실패한다 → 학생/강사 코스 화면 장애.
--   그래서 컬럼 추가 전까지 새 백엔드 코드는 배포하지 않고 대기 중이다(구 코드가 정상 서빙).
--
-- 아래 SQL 은 alembic 이 MySQL 에 실제로 내보내는 DDL 그대로다. ADD COLUMN 하나뿐이고 기존
-- 데이터를 읽거나 지우지 않는다(nullable, 기존 행은 전부 NULL = 커버 없음 → 기존 자동 커버 유지).
-- ============================================================================

-- 1) 코스 대표 썸네일 확장자 (없으면 NULL = 커버 미설정. 경로는 코드가 id+확장자로 유도)
ALTER TABLE courses ADD COLUMN thumbnail_ext VARCHAR(10);

-- 2) 마이그레이션 마커 갱신 — 위 1 이 성공한 뒤에만 실행
UPDATE alembic_version SET version_num = 'course_thumbnail_01';


-- ============================================================================
-- 실행 후 확인용 (모두 기대값이 나와야 함)
-- ============================================================================
-- 기대: 1
-- SELECT COUNT(*) FROM information_schema.columns
--  WHERE table_schema='catchap_dev_db' AND table_name='courses'
--    AND column_name='thumbnail_ext';
--
-- 기대: course_thumbnail_01
-- SELECT version_num FROM alembic_version;


-- ============================================================================
-- 되돌리기 (문제 시)
-- ============================================================================
-- UPDATE alembic_version SET version_num = 'lecture_report_01';
-- ALTER TABLE courses DROP COLUMN thumbnail_ext;
