-- ============================================================================
-- DBA 요청: lecture_watch_progress 에 bot_suspicion 컬럼 추가
--
-- 요청자   : 하지영 (백엔드) / 원 작성 최성우 (행동 기반 봇 판별)
-- 대상 DB  : catchap_dev_db  (프로덕션)
-- 리비전   : lecture_botsusp_01  (down_revision = course_payment_pg_02)
-- 작성일   : 2026-07-29
--
-- 왜 필요한가
--   인강 시청 중 이상행동(속도 위반·동시접속 충돌·체크포인트 연속 오답)을 누적해
--   임계를 넘으면 메인 캡차를 띄우는 기능. 그 누적값을 담는 컬럼이다.
--
-- 왜 백엔드 계정으로 못 하는가
--   앱 런타임 계정에 DDL 권한이 없다(SELECT/INSERT/UPDATE/DELETE 만).
--     GRANT SELECT, INSERT, UPDATE, DELETE ON catchap_dev_db.*
--   `alembic upgrade head` 를 앱 계정으로 돌리면 ALTER command denied 로 실패한다.
--   앱이 실수로 스키마를 바꾸지 못하게 분리한 설계이므로 그대로 두는 것이 맞다.
--
-- ⚠️ 배포 순서 (중요)
--   이 DDL 이 **코드 배포보다 먼저** 적용되어야 한다.
--   ORM 모델이 bot_suspicion 을 선언하므로 lecture_watch_progress 를 읽는
--   모든 SELECT 에 이 컬럼이 포함된다. 컬럼 없이 코드를 올리면
--   BOT_ESCALATION_MODE=off 여도 하트비트·진도·체크포인트가 전부
--   MySQL 1054 (Unknown column) 로 실패한다 — 인강 시청이 통째로 멈춘다.
--   (기능 플래그는 코드 경로만 끄지, ORM 의 SELECT 목록은 바꾸지 않는다.)
--
-- 영향 범위
--   컬럼 추가 1건. 기존 행은 server_default 0 으로 채워진다. 인덱스 변경 없음.
--   되돌리기: 기능은 BOT_ESCALATION_MODE=off 로 죽고, 컬럼은 남아도 무해하다.
-- ============================================================================

-- 1) 현재 상태 확인 (이미 있으면 2~3번은 건너뛴다)
SHOW COLUMNS FROM lecture_watch_progress LIKE 'bot_suspicion';

-- 2) 컬럼 추가
ALTER TABLE lecture_watch_progress
    ADD COLUMN bot_suspicion INTEGER NOT NULL DEFAULT '0';

-- 3) 마이그레이션 마커 갱신
--    현재 값이 'course_payment_pg_02' 인지 먼저 확인한 뒤 실행한다.
SELECT version_num FROM alembic_version;

UPDATE alembic_version
   SET version_num = 'lecture_botsusp_01'
 WHERE version_num = 'course_payment_pg_02';

-- 4) 적용 확인
SHOW COLUMNS FROM lecture_watch_progress LIKE 'bot_suspicion';
SELECT version_num FROM alembic_version;
--    기대: bot_suspicion / int / NO / (기본값 0)
--          version_num = 'lecture_botsusp_01'
