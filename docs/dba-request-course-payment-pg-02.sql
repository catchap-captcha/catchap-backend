-- ============================================================================
-- CatChap 운영 DB 스키마 변경 요청 — DBA 실행용
--
--   대상 DB   : catchap_dev_db  (10.0.1.168:3306)
--   마이그레이션: course_payment_pg_02  "카카오페이·토스페이먼츠 운영 결제 필드"
--   현재 상태 : alembic_version = course_order_01
--   변경 후   : alembic_version = course_payment_pg_02
--
-- 왜 DBA가 실행해야 하나
--   앱 계정 catchap_backend 의 권한은 SELECT/INSERT/UPDATE/DELETE 뿐이라
--   ALTER TABLE / CREATE INDEX 를 실행할 수 없다(ERROR 1142).
--     GRANT SELECT, INSERT, UPDATE, DELETE ON `catchap_dev_db`.* TO `catchap_backend`@`%`
--
-- 왜 지금 필요한가
--   새 백엔드 코드의 ORM 이 courses / course_orders 를 조회할 때 아래 컬럼을 SELECT 목록에
--   포함한다. 이 변경을 먼저 적용하지 않고 코드를 배포하면 코스를 읽는 모든 요청이
--   MySQL 1054(Unknown column)로 실패한다 → 학생 화면 전체 장애.
--
-- 아래 SQL 은 alembic 이 MySQL 에 실제로 내보내는 DDL 을 그대로 렌더링한 것이다.
-- 모두 ADD COLUMN / CREATE INDEX 이며 기존 데이터를 읽거나 지우지 않는다.
-- (실행 시점 기준 courses 8행, course_orders 3행 — 잠금 시간은 무시할 수준)
-- ============================================================================

-- 1) 코스 가격 (결제 금액의 서버 정본. 0 = 무료, 기존 8개 코스는 전부 0으로 채워진다)
ALTER TABLE courses ADD COLUMN price INTEGER NOT NULL DEFAULT '0';
ALTER TABLE courses ADD COLUMN sale_price INTEGER;
ALTER TABLE courses ADD COLUMN sale_ends_at DATETIME;

-- 2) PG 결제 주문 필드 (카카오페이 콜백 state 해시 · 임시 세션 · 영수증 · 취소 기록)
ALTER TABLE course_orders ADD COLUMN callback_token_hash VARCHAR(64);
ALTER TABLE course_orders ADD COLUMN provider_session JSON;
ALTER TABLE course_orders ADD COLUMN receipt_url VARCHAR(500);
ALTER TABLE course_orders ADD COLUMN cancelled_at DATETIME;
ALTER TABLE course_orders ADD COLUMN cancel_reason VARCHAR(200);

-- 3) PG 결제 식별값 조회 인덱스 (웹훅이 paymentKey 로 주문을 되찾을 때)
CREATE INDEX ix_order_provider_payment_key ON course_orders (provider, payment_key);

-- 4) 마이그레이션 마커 갱신 — 위 1~3 이 모두 성공한 뒤에만 실행
UPDATE alembic_version SET version_num = 'course_payment_pg_02';


-- ============================================================================
-- 실행 후 확인용 (모두 기대값이 나와야 함)
-- ============================================================================
-- 기대: 3
-- SELECT COUNT(*) FROM information_schema.columns
--  WHERE table_schema='catchap_dev_db' AND table_name='courses'
--    AND column_name IN ('price','sale_price','sale_ends_at');
--
-- 기대: 5
-- SELECT COUNT(*) FROM information_schema.columns
--  WHERE table_schema='catchap_dev_db' AND table_name='course_orders'
--    AND column_name IN ('callback_token_hash','provider_session','receipt_url',
--                        'cancelled_at','cancel_reason');
--
-- 기대: 1
-- SELECT COUNT(*) FROM information_schema.statistics
--  WHERE table_schema='catchap_dev_db' AND table_name='course_orders'
--    AND index_name='ix_order_provider_payment_key';
--
-- 기대: course_payment_pg_02
-- SELECT version_num FROM alembic_version;


-- ============================================================================
-- 되돌리기 (문제 시)
-- ============================================================================
-- UPDATE alembic_version SET version_num = 'course_order_01';
-- DROP INDEX ix_order_provider_payment_key ON course_orders;
-- ALTER TABLE course_orders DROP COLUMN cancel_reason;
-- ALTER TABLE course_orders DROP COLUMN cancelled_at;
-- ALTER TABLE course_orders DROP COLUMN receipt_url;
-- ALTER TABLE course_orders DROP COLUMN provider_session;
-- ALTER TABLE course_orders DROP COLUMN callback_token_hash;
-- ALTER TABLE courses DROP COLUMN sale_ends_at;
-- ALTER TABLE courses DROP COLUMN sale_price;
-- ALTER TABLE courses DROP COLUMN price;
