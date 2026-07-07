-- ============================================================
-- CatChap 캣챱 — DB 설치 스크립트 (MySQL 8.x) · 팀원용 상세판
-- 생성일: 2026-07-07 (백엔드 SQLAlchemy 모델에서 자동 추출 — 코드와 100% 일치)
-- 테이블 52개 · 검증: 임시 DB에 전체 실행하여 52/52 생성 확인함
--
-- ── 설치 순서 ──────────────────────────────────────────────
--   1) MySQL 8.x 설치 후 root(관리자)로 접속
--   2) 아래 'CHANGE_ME' 비밀번호를 팀 비밀번호로 바꾼 뒤 전체 실행:
--        mysql -u root -p < db-setup.sql
--   3) catchap-backend/.env 의 접속 문자열을 맞춘다:
--        DATABASE_URL=mysql+pymysql://catchap:비밀번호@localhost:3306/catchap?charset=utf8mb4
--   4) (선택) 데모 데이터 넣기: catchap-backend 폴더에서
--        python -m app.db.seed
--   5) (선택) 마이그레이션 이력 맞추기: alembic stamp head
--
-- ── 꼭 지킬 것 ─────────────────────────────────────────────
--   · 문자셋 utf8mb4 필수 (한글·이모지 깨짐 방지)
--   · 엔진 InnoDB 필수 (외래키/트랜잭션)
--   · PK는 전부 CHAR(36) UUID — 백엔드가 생성해서 넣는다
--   · 비밀번호/코드/토큰류는 전부 해시로만 저장된다 (원문 컬럼 없음)
--
-- ── 테이블 한눈에 보기 (11개 영역 · 52개) ──────────────────
--   [계정·인증] email_verification_codes, login_throttle, password_reset_tokens, refresh_tokens, student_profiles, user_settings, users
--   [기관·학급] classes, institutions, invitations, memberships, org_registration_requests, organizations
--   [온보딩 코드] parent_invite_codes, student_join_codes
--   [학부모] family_messages, parent_student_links, report_download_logs, reports
--   [학습 콘텐츠] chapters, contents
--   [학습 기록] concept_reads, daily_quiz_status, learning_attempts, learning_summaries, recommendations, student_progress, wrong_answers
--   [보상·랭킹] badges, coin_transactions, shop_items, student_badges, student_items
--   [알림·문의] email_logs, inquiries, inquiry_replies, notifications
--   [캡차·API] ai_predictions, api_keys, api_usage_logs, behavior_summaries, captcha_assets, captcha_settings, model_versions, sites
--   [과금] invoices, payment_methods, plans, subscriptions
--   [운영·감사] audit_logs, stat_blobs, system_health_logs
-- ============================================================

CREATE DATABASE IF NOT EXISTS catchap
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'catchap'@'%' IDENTIFIED BY 'CHANGE_ME';
GRANT ALL PRIVILEGES ON catchap.* TO 'catchap'@'%';
FLUSH PRIVILEGES;

USE catchap;

-- ─────────────────────────────────────────────
-- ▶ api_usage_logs  [캡차·API]
--   무엇: API 호출 로그(엔드포인트·상태·지연)
--   왜: 기관 대시보드 사용량 그래프의 원천
-- ─────────────────────────────────────────────
CREATE TABLE api_usage_logs (
	organization_id CHAR(36) NOT NULL, 
	site_id CHAR(36), 
	endpoint VARCHAR(150) NOT NULL, 
	method VARCHAR(10) NOT NULL, 
	status_code INTEGER NOT NULL, 
	latency_ms INTEGER NOT NULL, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_api_usage_logs_organization_id ON api_usage_logs (organization_id);
CREATE INDEX ix_api_usage_logs_site_id ON api_usage_logs (site_id);
CREATE INDEX ix_aul_org_created ON api_usage_logs (organization_id, created_at);

-- ─────────────────────────────────────────────
-- ▶ audit_logs  [운영·감사]
--   무엇: 민감 행동 감사 로그(승인·비번초기화·임명/해제·해체 등 before/after)
--   왜: 누가 언제 무엇을 바꿨는지 추적 — 운영자 감사 콘솔의 원천
-- ─────────────────────────────────────────────
CREATE TABLE audit_logs (
	organization_id CHAR(36), 
	actor_user_id CHAR(36), 
	action VARCHAR(60) NOT NULL, 
	target_type VARCHAR(40), 
	target_id CHAR(36), 
	before_json JSON, 
	after_json JSON, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_audit_logs_action ON audit_logs (action);
CREATE INDEX ix_audit_logs_actor_user_id ON audit_logs (actor_user_id);
CREATE INDEX ix_audit_logs_organization_id ON audit_logs (organization_id);

-- ─────────────────────────────────────────────
-- ▶ badges  [보상·랭킹]
--   무엇: 배지 정의(개근왕 등 조건·아이콘)
--   왜: 획득 가능한 배지의 마스터 목록
-- ─────────────────────────────────────────────
CREATE TABLE badges (
	name VARCHAR(60) NOT NULL, 
	description VARCHAR(200) NOT NULL, 
	icon VARCHAR(60) NOT NULL, 
	color VARCHAR(20) NOT NULL, 
	condition_text VARCHAR(200) NOT NULL, 
	order_no INTEGER NOT NULL, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ─────────────────────────────────────────────
-- ▶ behavior_summaries  [캡차·API]
--   무엇: 행동 데이터 요약(기관 분석 화면용)
--   왜: 조작실패율 등 대시보드 지표 원천
-- ─────────────────────────────────────────────
CREATE TABLE behavior_summaries (
	organization_id CHAR(36) NOT NULL, 
	student_id CHAR(36), 
	source_type VARCHAR(30) NOT NULL, 
	solve_time_ms INTEGER NOT NULL, 
	path_length FLOAT NOT NULL, 
	avg_speed FLOAT NOT NULL, 
	pause_count INTEGER NOT NULL, 
	retry_count INTEGER NOT NULL, 
	drop_distance_norm FLOAT NOT NULL, 
	interaction_result VARCHAR(20),
	risk_level VARCHAR(20) NOT NULL,
	occurred_at DATETIME,
	dataset_status VARCHAR(20) NOT NULL DEFAULT 'candidate',
	id CHAR(36) NOT NULL,
	created_at DATETIME NOT NULL DEFAULT now(),
	updated_at DATETIME NOT NULL DEFAULT now(),
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_behavior_summaries_organization_id ON behavior_summaries (organization_id);
CREATE INDEX ix_behavior_summaries_student_id ON behavior_summaries (student_id);
CREATE INDEX ix_bs_created ON behavior_summaries (created_at);

-- ─────────────────────────────────────────────
-- ▶ behavior_traces  [캡차·API]
--   무엇: 원시 포인터 궤적 [[t,x,y],...] (behavior_summaries 1행당 최대 1행)
--   왜: 아동용 캡차 판정 모델의 학습 재료 — 요약 지표는 서버가 이 궤적에서 계산
-- ─────────────────────────────────────────────
CREATE TABLE behavior_traces (
	behavior_id CHAR(36) NOT NULL,
	points JSON NOT NULL,
	point_count INTEGER NOT NULL DEFAULT 0,
	duration_ms INTEGER NOT NULL DEFAULT 0,
	box_w INTEGER NOT NULL DEFAULT 0,
	box_h INTEGER NOT NULL DEFAULT 0,
	id CHAR(36) NOT NULL,
	created_at DATETIME NOT NULL DEFAULT now(),
	updated_at DATETIME NOT NULL DEFAULT now(),
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE UNIQUE INDEX ix_behavior_traces_behavior_id ON behavior_traces (behavior_id);

-- ─────────────────────────────────────────────
-- ▶ captcha_assets  [캡차·API]
--   무엇: 캡차 문제 소재(이미지 등) 메타
--   왜: 캡차 출제 풀(메인 엔진은 다음 단계)
-- ─────────────────────────────────────────────
CREATE TABLE captcha_assets (
	organization_id CHAR(36), 
	file_url VARCHAR(255) NOT NULL, 
	file_name VARCHAR(255) NOT NULL, 
	file_type VARCHAR(30) NOT NULL, 
	category VARCHAR(30), 
	ai_label VARCHAR(60), 
	review_status VARCHAR(20) NOT NULL, 
	approved_by CHAR(36), 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_captcha_assets_organization_id ON captcha_assets (organization_id);

-- ─────────────────────────────────────────────
-- ▶ chapters  [학습 콘텐츠]
--   무엇: 과목별 챕터(단원) 정의
--   왜: 챕터 지도의 구조 데이터
-- ─────────────────────────────────────────────
CREATE TABLE chapters (
	subject VARCHAR(20) NOT NULL, 
	order_no INTEGER NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	total_questions INTEGER NOT NULL, 
	concept JSON NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_chapters_subject ON chapters (subject);

-- ─────────────────────────────────────────────
-- ▶ contents  [학습 콘텐츠]
--   무엇: 학습 콘텐츠(문제/개념) 메타
--   왜: 검색·추천이 참조하는 콘텐츠 원장
-- ─────────────────────────────────────────────
CREATE TABLE contents (
	organization_id CHAR(36), 
	title VARCHAR(150) NOT NULL, 
	description TEXT, 
	category VARCHAR(30) NOT NULL, 
	subject VARCHAR(20), 
	difficulty INTEGER NOT NULL, 
	age_group VARCHAR(30) NOT NULL, 
	icon VARCHAR(60), 
	route_hint VARCHAR(120), 
	status VARCHAR(20) NOT NULL, 
	created_by CHAR(36), 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_contents_category ON contents (category);
CREATE INDEX ix_contents_organization_id ON contents (organization_id);
CREATE INDEX ix_contents_subject ON contents (subject);

-- ─────────────────────────────────────────────
-- ▶ email_logs  [알림·문의]
--   무엇: 발송한 이메일 로그
--   왜: 인증코드/답변 메일 발송 추적(디버깅·감사)
-- ─────────────────────────────────────────────
CREATE TABLE email_logs (
	user_id CHAR(36), 
	to_email VARCHAR(255) NOT NULL, 
	subject VARCHAR(200) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	error_message TEXT, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_email_logs_user_id ON email_logs (user_id);

-- ─────────────────────────────────────────────
-- ▶ email_verification_codes  [계정·인증]
--   무엇: 회원가입 이메일 인증 6자리 코드(해시, 만료)
--   왜: 가짜 이메일 가입 차단. 원문 미저장(sha256)
-- ─────────────────────────────────────────────
CREATE TABLE email_verification_codes (
	email VARCHAR(255) NOT NULL, 
	user_id CHAR(36), 
	purpose VARCHAR(20) NOT NULL, 
	code_hash VARCHAR(64) NOT NULL, 
	expires_at DATETIME NOT NULL, 
	used_at DATETIME, 
	verified_at DATETIME, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_email_verification_codes_code_hash ON email_verification_codes (code_hash);
CREATE INDEX ix_email_verification_codes_email ON email_verification_codes (email);
CREATE INDEX ix_email_verification_codes_user_id ON email_verification_codes (user_id);

-- ─────────────────────────────────────────────
-- ▶ inquiries  [알림·문의]
--   무엇: 문의 접수(공개 문의하기 + 기관 관리자 문의 폼)
--   왜: 운영자 문의 처리 콘솔의 원천
-- ─────────────────────────────────────────────
CREATE TABLE inquiries (
	inquiry_type VARCHAR(30) NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	affiliation VARCHAR(150), 
	email VARCHAR(255) NOT NULL, 
	content TEXT NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ─────────────────────────────────────────────
-- ▶ institutions  [기관·학급]
--   무엇: 전국 기관 디렉토리(시도>시군구>동 검색용)
--   왜: 회원가입 때 '소속 기관 찾기' 검색 데이터
-- ─────────────────────────────────────────────
CREATE TABLE institutions (
	name VARCHAR(150) NOT NULL, 
	inst_type VARCHAR(30) NOT NULL, 
	sido VARCHAR(30) NOT NULL, 
	sigungu VARCHAR(30) NOT NULL, 
	dong VARCHAR(30) NOT NULL, 
	road_address VARCHAR(255) NOT NULL, 
	organization_id CHAR(36), 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_institutions_dong ON institutions (dong);
CREATE INDEX ix_institutions_name ON institutions (name);
CREATE INDEX ix_institutions_sido ON institutions (sido);
CREATE INDEX ix_institutions_sigungu ON institutions (sigungu);

-- ─────────────────────────────────────────────
-- ▶ invoices  [과금]
--   무엇: 청구서/결제 내역
--   왜: 기관 마이페이지 결제 화면 데이터
-- ─────────────────────────────────────────────
CREATE TABLE invoices (
	organization_id CHAR(36) NOT NULL, 
	invoice_no VARCHAR(30) NOT NULL, 
	description VARCHAR(150) NOT NULL, 
	amount INTEGER NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	billed_on VARCHAR(20), 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	UNIQUE (invoice_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_invoices_organization_id ON invoices (organization_id);

-- ─────────────────────────────────────────────
-- ▶ login_throttle  [계정·인증]
--   무엇: 로그인/코드입력 실패 카운터 (식별자별 10회/15분 잠금)
--   왜: 무차별 대입(brute-force) 공격 차단
-- ─────────────────────────────────────────────
CREATE TABLE login_throttle (
	identifier VARCHAR(255) NOT NULL, 
	fail_count INTEGER NOT NULL, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE UNIQUE INDEX ix_login_throttle_identifier ON login_throttle (identifier);

-- ─────────────────────────────────────────────
-- ▶ model_versions  [캡차·API]
--   무엇: 행동분석 AI 모델 버전 레지스트리
--   왜: AI 모델 화면 표시(AI 서비스는 다음 단계)
-- ─────────────────────────────────────────────
CREATE TABLE model_versions (
	category VARCHAR(60) NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	provider VARCHAR(60) NOT NULL, 
	version VARCHAR(30) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	description TEXT, 
	updated_on VARCHAR(30), 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ─────────────────────────────────────────────
-- ▶ notifications  [알림·문의]
--   무엇: 역할별 알림(학부모 연동 알림 parent_link 포함)
--   왜: 학생 연동 팝업·알림 화면의 데이터
-- ─────────────────────────────────────────────
CREATE TABLE notifications (
	user_id CHAR(36), 
	student_id CHAR(36), 
	organization_id CHAR(36), 
	type VARCHAR(30) NOT NULL, 
	category VARCHAR(30) NOT NULL, 
	title VARCHAR(150) NOT NULL, 
	message TEXT NOT NULL, 
	child_id CHAR(36), 
	read_at DATETIME, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_notifications_organization_id ON notifications (organization_id);
CREATE INDEX ix_notifications_student_id ON notifications (student_id);
CREATE INDEX ix_notifications_user_id ON notifications (user_id);

-- ─────────────────────────────────────────────
-- ▶ org_registration_requests  [기관·학급]
--   무엇: 기관 가입 신청서(운영자 승인 대기 pending→approved)
--   왜: 아무나 기관을 못 만들도록 운영자 승인 절차를 거침
-- ─────────────────────────────────────────────
CREATE TABLE org_registration_requests (
	org_name VARCHAR(150) NOT NULL, 
	org_type VARCHAR(30) NOT NULL, 
	business_number VARCHAR(30), 
	address VARCHAR(255), 
	contact_name VARCHAR(100) NOT NULL, 
	contact_email VARCHAR(255) NOT NULL, 
	contact_phone VARCHAR(30), 
	expected_students VARCHAR(30), 
	plan_interest VARCHAR(30), 
	status VARCHAR(20) NOT NULL, 
	approved_at DATETIME, 
	organization_id CHAR(36), 
	memo TEXT, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ─────────────────────────────────────────────
-- ▶ organizations  [기관·학급]
--   무엇: 등록된 고객 기관(학교/유치원). 기관 가입코드(code)와 만료일 포함
--   왜: 멀티테넌트의 뿌리 — 모든 데이터가 organization_id로 격리됨
-- ─────────────────────────────────────────────
CREATE TABLE organizations (
	name VARCHAR(150) NOT NULL, 
	code VARCHAR(30) NOT NULL, 
	org_type VARCHAR(30) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	contact_email VARCHAR(255), 
	contact_phone VARCHAR(30), 
	address VARCHAR(255), 
	business_number VARCHAR(30), 
	code_expires_at DATETIME, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	UNIQUE (business_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE UNIQUE INDEX ix_organizations_code ON organizations (code);

-- ─────────────────────────────────────────────
-- ▶ parent_invite_codes  [온보딩 코드]
--   무엇: 학부모 초대코드(LINK-, 해시, 만료, 최대 2회)
--   왜: 학부모-자녀 연결을 학교가 통제 — 학생코드 추측으로 무단 연결되는 것 차단
-- ─────────────────────────────────────────────
CREATE TABLE parent_invite_codes (
	student_id CHAR(36) NOT NULL, 
	organization_id CHAR(36) NOT NULL, 
	code_hash VARCHAR(64) NOT NULL, 
	expires_at DATETIME, 
	max_uses INTEGER NOT NULL, 
	used_count INTEGER NOT NULL, 
	revoked_at DATETIME, 
	created_by CHAR(36), 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_parent_invite_codes_code_hash ON parent_invite_codes (code_hash);
CREATE INDEX ix_parent_invite_codes_organization_id ON parent_invite_codes (organization_id);
CREATE INDEX ix_parent_invite_codes_student_id ON parent_invite_codes (student_id);

-- ─────────────────────────────────────────────
-- ▶ password_reset_tokens  [계정·인증]
--   무엇: 비밀번호 재설정 토큰(해시, 만료)
--   왜: 이메일 링크 재설정 흐름의 1회용 토큰
-- ─────────────────────────────────────────────
CREATE TABLE password_reset_tokens (
	user_id CHAR(36) NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	expires_at DATETIME NOT NULL, 
	used_at DATETIME, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	UNIQUE (token_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_password_reset_tokens_user_id ON password_reset_tokens (user_id);

-- ─────────────────────────────────────────────
-- ▶ payment_methods  [과금]
--   무엇: 등록된 결제 수단(마스킹)
--   왜: 결제 수단 관리 화면 데이터
-- ─────────────────────────────────────────────
CREATE TABLE payment_methods (
	organization_id CHAR(36) NOT NULL, 
	card_brand VARCHAR(30) NOT NULL, 
	card_last4 VARCHAR(4) NOT NULL, 
	is_default BOOL NOT NULL, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_payment_methods_organization_id ON payment_methods (organization_id);

-- ─────────────────────────────────────────────
-- ▶ plans  [과금]
--   무엇: 요금제 정의(basic/pro 등)
--   왜: 구독 상품 마스터
-- ─────────────────────────────────────────────
CREATE TABLE plans (
	`key` VARCHAR(30) NOT NULL, 
	name VARCHAR(60) NOT NULL, 
	monthly_price INTEGER NOT NULL, 
	yearly_price INTEGER NOT NULL, 
	api_quota INTEGER NOT NULL, 
	student_seats INTEGER NOT NULL, 
	teacher_seats INTEGER NOT NULL, 
	features JSON NOT NULL, 
	order_no INTEGER NOT NULL, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	UNIQUE (`key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ─────────────────────────────────────────────
-- ▶ refresh_tokens  [계정·인증]
--   무엇: JWT 리프레시 토큰(해시) 저장·회전·폐기
--   왜: 로그아웃/비번초기화 시 모든 기기 세션을 즉시 끊기 위해 필요
-- ─────────────────────────────────────────────
CREATE TABLE refresh_tokens (
	user_id CHAR(36) NOT NULL, 
	subject_type VARCHAR(10) NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	expires_at DATETIME NOT NULL, 
	revoked_at DATETIME, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	UNIQUE (token_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_refresh_tokens_user_id ON refresh_tokens (user_id);

-- ─────────────────────────────────────────────
-- ▶ reports  [학부모]
--   무엇: 생성된 학습 리포트 메타(자녀·기간·파일)
--   왜: 학부모 리포트 다운로드 목록의 원천
-- ─────────────────────────────────────────────
CREATE TABLE reports (
	organization_id CHAR(36) NOT NULL, 
	student_id CHAR(36), 
	report_type VARCHAR(30) NOT NULL, 
	period_start DATETIME, 
	period_end DATETIME, 
	status VARCHAR(20) NOT NULL, 
	file_url VARCHAR(255), 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_reports_organization_id ON reports (organization_id);
CREATE INDEX ix_reports_student_id ON reports (student_id);

-- ─────────────────────────────────────────────
-- ▶ shop_items  [보상·랭킹]
--   무엇: 아바타 상점 아이템 정의
--   왜: 프로필 꾸미기 상점 목록
-- ─────────────────────────────────────────────
CREATE TABLE shop_items (
	category VARCHAR(20) NOT NULL, 
	name VARCHAR(60) NOT NULL, 
	icon VARCHAR(60) NOT NULL, 
	price INTEGER NOT NULL, 
	order_no INTEGER NOT NULL, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_shop_items_category ON shop_items (category);

-- ─────────────────────────────────────────────
-- ▶ stat_blobs  [운영·감사]
--   무엇: 화면 데모/보정용 통계 JSON(키-값)
--   왜: 실데이터가 없는 항목의 그래프·KPI를 채우는 디자인 값 저장소(5초 캐시)
-- ─────────────────────────────────────────────
CREATE TABLE stat_blobs (
	organization_id CHAR(36), 
	`key` VARCHAR(80) NOT NULL, 
	payload JSON NOT NULL, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	CONSTRAINT uq_stat_org_key UNIQUE (organization_id, `key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_stat_blobs_key ON stat_blobs (`key`);
CREATE INDEX ix_stat_blobs_organization_id ON stat_blobs (organization_id);

-- ─────────────────────────────────────────────
-- ▶ student_join_codes  [온보딩 코드]
--   무엇: 학교 발급 1회용 학생 가입코드(JOIN-, 해시)·발급된 로그인아이디·실명·배정 반
--   왜: 학생이 이메일 없이 코드만으로 안전하게 계정을 활성화하는 핵심 장치
-- ─────────────────────────────────────────────
CREATE TABLE student_join_codes (
	organization_id CHAR(36) NOT NULL, 
	class_id CHAR(36), 
	login_id VARCHAR(60) NOT NULL, 
	code_hash VARCHAR(64) NOT NULL, 
	class_label VARCHAR(60), 
	real_name VARCHAR(100), 
	expires_at DATETIME, 
	used_at DATETIME, 
	student_id CHAR(36), 
	created_by CHAR(36), 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_student_join_codes_class_id ON student_join_codes (class_id);
CREATE INDEX ix_student_join_codes_code_hash ON student_join_codes (code_hash);
CREATE UNIQUE INDEX ix_student_join_codes_login_id ON student_join_codes (login_id);
CREATE INDEX ix_student_join_codes_organization_id ON student_join_codes (organization_id);

-- ─────────────────────────────────────────────
-- ▶ system_health_logs  [운영·감사]
--   무엇: 시스템 상태 로그
--   왜: 운영 대시보드 서비스 상태 표시용
-- ─────────────────────────────────────────────
CREATE TABLE system_health_logs (
	service_name VARCHAR(60) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	latency_ms INTEGER NOT NULL, 
	checked_at DATETIME, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ─────────────────────────────────────────────
-- ▶ user_settings  [계정·인증]
--   무엇: 사용자별 화면 설정(알림 on/off 등) key-value
--   왜: 설정 화면 저장값 보관
-- ─────────────────────────────────────────────
CREATE TABLE user_settings (
	subject_type VARCHAR(10) NOT NULL, 
	subject_id CHAR(36) NOT NULL, 
	settings JSON NOT NULL, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_user_settings_subject_id ON user_settings (subject_id);

-- ─────────────────────────────────────────────
-- ▶ users  [계정·인증]
--   무엇: 이메일 계정 사용자(학부모/교사/학년부장/기관관리자/운영자). 역할(role)과 상태 보관
--   왜: 학생 외 모든 로그인 주체의 원장. RBAC 권한 판정의 기준
-- ─────────────────────────────────────────────
CREATE TABLE users (
	email VARCHAR(255), 
	password_hash VARCHAR(255) NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	phone VARCHAR(30), 
	`role` VARCHAR(20) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	email_verified_at DATETIME, 
	last_login_at DATETIME, 
	two_factor_enabled BOOL NOT NULL, 
	organization_id CHAR(36), 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE UNIQUE INDEX ix_users_email ON users (email);
CREATE INDEX ix_users_organization_id ON users (organization_id);
CREATE INDEX ix_users_role ON users (`role`);

-- ─────────────────────────────────────────────
-- ▶ ai_predictions  [캡차·API]
--   무엇: AI 행동분석 예측 결과 저장(스텁)
--   왜: 봇 판정/이상행동 결과가 쌓일 자리
-- ─────────────────────────────────────────────
CREATE TABLE ai_predictions (
	asset_id CHAR(36) NOT NULL, 
	model_version VARCHAR(30) NOT NULL, 
	predicted_label VARCHAR(60) NOT NULL, 
	confidence FLOAT NOT NULL, 
	latency_ms INTEGER NOT NULL, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(asset_id) REFERENCES captcha_assets (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_ai_predictions_asset_id ON ai_predictions (asset_id);

-- ─────────────────────────────────────────────
-- ▶ captcha_settings  [캡차·API]
--   무엇: 기관별 캡차 종류 on/off·라운드 수·셔플
--   왜: 관리자가 어떤 캡차를 작동시킬지 제어하는 설정
-- ─────────────────────────────────────────────
CREATE TABLE captcha_settings (
	organization_id CHAR(36) NOT NULL, 
	active_types JSON NOT NULL, 
	round_count INTEGER NOT NULL, 
	shuffle BOOL NOT NULL, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(organization_id) REFERENCES organizations (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE UNIQUE INDEX ix_captcha_settings_organization_id ON captcha_settings (organization_id);

-- ─────────────────────────────────────────────
-- ▶ classes  [기관·학급]
--   무엇: 학급(반). 학년(grade)·담임(teacher_id)·보조담임(assistant_teacher_id)·상태(active/archived)
--   왜: 반 배정·학년 랭킹·학년부장 스코프의 기준 단위. 학년말 해체=archived
-- ─────────────────────────────────────────────
CREATE TABLE classes (
	organization_id CHAR(36) NOT NULL, 
	name VARCHAR(50) NOT NULL, 
	grade INTEGER, 
	age_group VARCHAR(30), 
	teacher_id CHAR(36), 
	assistant_teacher_id CHAR(36), 
	status VARCHAR(20) NOT NULL, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(organization_id) REFERENCES organizations (id), 
	FOREIGN KEY(teacher_id) REFERENCES users (id), 
	FOREIGN KEY(assistant_teacher_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_classes_assistant_teacher_id ON classes (assistant_teacher_id);
CREATE INDEX ix_classes_organization_id ON classes (organization_id);
CREATE INDEX ix_classes_teacher_id ON classes (teacher_id);

-- ─────────────────────────────────────────────
-- ▶ inquiry_replies  [알림·문의]
--   무엇: 문의에 대한 운영자 답변(1:N)
--   왜: 답변 이력 보관·이메일 발송 기록
-- ─────────────────────────────────────────────
CREATE TABLE inquiry_replies (
	inquiry_id CHAR(36) NOT NULL, 
	body TEXT NOT NULL, 
	answered_by CHAR(36), 
	email_status VARCHAR(20) NOT NULL, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(inquiry_id) REFERENCES inquiries (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_inquiry_replies_inquiry_id ON inquiry_replies (inquiry_id);

-- ─────────────────────────────────────────────
-- ▶ invitations  [기관·학급]
--   무엇: 이메일 기반 기관 초대(토큰 해시)
--   왜: 관리자가 교사 등을 이메일로 초대하는 흐름용
-- ─────────────────────────────────────────────
CREATE TABLE invitations (
	organization_id CHAR(36) NOT NULL, 
	email VARCHAR(255) NOT NULL, 
	`role` VARCHAR(20) NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	invited_by CHAR(36), 
	expires_at DATETIME NOT NULL, 
	accepted_at DATETIME, 
	status VARCHAR(20) NOT NULL, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(organization_id) REFERENCES organizations (id), 
	UNIQUE (token_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_invitations_email ON invitations (email);
CREATE INDEX ix_invitations_organization_id ON invitations (organization_id);

-- ─────────────────────────────────────────────
-- ▶ memberships  [기관·학급]
--   무엇: 기관 소속(교사/학년부장/기관관리자) + 교사코드(T-xxxx)·담당학년(managed_grade)
--   왜: 한 사람의 기관 내 역할·권한 범위를 정의. 학년부장 스코프의 근거
-- ─────────────────────────────────────────────
CREATE TABLE memberships (
	user_id CHAR(36), 
	organization_id CHAR(36) NOT NULL, 
	`role` VARCHAR(20) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	teacher_code VARCHAR(20), 
	position VARCHAR(50), 
	managed_grade INTEGER, 
	career_years INTEGER, 
	invited_by CHAR(36), 
	joined_at DATETIME, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(organization_id) REFERENCES organizations (id), 
	UNIQUE (teacher_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_memberships_organization_id ON memberships (organization_id);
CREATE INDEX ix_memberships_user_id ON memberships (user_id);

-- ─────────────────────────────────────────────
-- ▶ report_download_logs  [학부모]
--   무엇: 리포트 다운로드 기록(누가·언제)
--   왜: 아동 데이터 열람 추적(감사 목적)
-- ─────────────────────────────────────────────
CREATE TABLE report_download_logs (
	report_id CHAR(36) NOT NULL, 
	user_id CHAR(36) NOT NULL, 
	downloaded_at DATETIME, 
	ip_address VARCHAR(45), 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(report_id) REFERENCES reports (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_report_download_logs_report_id ON report_download_logs (report_id);
CREATE INDEX ix_report_download_logs_user_id ON report_download_logs (user_id);

-- ─────────────────────────────────────────────
-- ▶ sites  [캡차·API]
--   무엇: 기관이 캡차를 붙일 사이트 등록
--   왜: 사이트키 발급 대상 — 캡차 위젯 연동 단위
-- ─────────────────────────────────────────────
CREATE TABLE sites (
	organization_id CHAR(36) NOT NULL, 
	name VARCHAR(150) NOT NULL, 
	domain VARCHAR(255) NOT NULL, 
	allowed_origins JSON NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(organization_id) REFERENCES organizations (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_sites_organization_id ON sites (organization_id);

-- ─────────────────────────────────────────────
-- ▶ subscriptions  [과금]
--   무엇: 기관별 구독 상태
--   왜: 어떤 기관이 어떤 요금제인지
-- ─────────────────────────────────────────────
CREATE TABLE subscriptions (
	organization_id CHAR(36) NOT NULL, 
	plan_id CHAR(36) NOT NULL, 
	billing_cycle VARCHAR(10) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	auto_renew BOOL NOT NULL, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(organization_id) REFERENCES organizations (id), 
	FOREIGN KEY(plan_id) REFERENCES plans (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE UNIQUE INDEX ix_subscriptions_organization_id ON subscriptions (organization_id);

-- ─────────────────────────────────────────────
-- ▶ api_keys  [캡차·API]
--   무엇: 사이트별 API 키(해시)
--   왜: 외부 사이트가 캡차 API를 호출할 때 인증
-- ─────────────────────────────────────────────
CREATE TABLE api_keys (
	organization_id CHAR(36) NOT NULL, 
	site_id CHAR(36) NOT NULL, 
	site_key VARCHAR(64) NOT NULL, 
	secret_key_hash VARCHAR(64) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	last_used_at DATETIME, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(site_id) REFERENCES sites (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_api_keys_organization_id ON api_keys (organization_id);
CREATE INDEX ix_api_keys_site_id ON api_keys (site_id);
CREATE UNIQUE INDEX ix_api_keys_site_key ON api_keys (site_key);

-- ─────────────────────────────────────────────
-- ▶ student_profiles  [계정·인증]
--   무엇: 학생 계정(닉네임 중심, 아이디+비번 로그인). real_name은 교사·기관 화면 전용 실명
--   왜: 아동 PII 최소화를 위해 users와 분리 — 이메일 없이 학교 발급 아이디로만 로그인
-- ─────────────────────────────────────────────
CREATE TABLE student_profiles (
	must_change_password BOOL NOT NULL, 
	organization_id CHAR(36) NOT NULL, 
	class_id CHAR(36), 
	student_login_id VARCHAR(50) NOT NULL, 
	student_code VARCHAR(20) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	nickname VARCHAR(50) NOT NULL, 
	real_name VARCHAR(100), 
	age INTEGER, 
	grade_band VARCHAR(30) NOT NULL, 
	avatar JSON NOT NULL, 
	coins INTEGER NOT NULL, 
	level INTEGER NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	last_login_at DATETIME, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(organization_id) REFERENCES organizations (id), 
	FOREIGN KEY(class_id) REFERENCES classes (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_student_profiles_class_id ON student_profiles (class_id);
CREATE INDEX ix_student_profiles_organization_id ON student_profiles (organization_id);
CREATE UNIQUE INDEX ix_student_profiles_student_code ON student_profiles (student_code);
CREATE UNIQUE INDEX ix_student_profiles_student_login_id ON student_profiles (student_login_id);

-- ─────────────────────────────────────────────
-- ▶ coin_transactions  [보상·랭킹]
--   무엇: 코인 적립/사용 내역(학습 보상·랭킹 보상·상점 구매)
--   왜: 코인 잔액의 회계 장부 — 파밍 상한(하루 300)과 랭킹 보너스(하루 1회) 판정에 사용
-- ─────────────────────────────────────────────
CREATE TABLE coin_transactions (
	student_id CHAR(36) NOT NULL, 
	amount INTEGER NOT NULL, 
	reason VARCHAR(100) NOT NULL, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(student_id) REFERENCES student_profiles (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_coin_transactions_student_id ON coin_transactions (student_id);

-- ─────────────────────────────────────────────
-- ▶ concept_reads  [학습 기록]
--   무엇: 개념 설명 읽음 체크
--   왜: 개념 학습 진행 표시용
-- ─────────────────────────────────────────────
CREATE TABLE concept_reads (
	student_id CHAR(36) NOT NULL, 
	chapter_id CHAR(36) NOT NULL, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(student_id) REFERENCES student_profiles (id), 
	FOREIGN KEY(chapter_id) REFERENCES chapters (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_concept_reads_chapter_id ON concept_reads (chapter_id);
CREATE INDEX ix_concept_reads_student_id ON concept_reads (student_id);

-- ─────────────────────────────────────────────
-- ▶ daily_quiz_status  [학습 기록]
--   무엇: 오늘의퀴즈 과목별 완료 상태(학생×날짜×과목)
--   왜: 일일 잠금 규칙과 **랭킹 점수(일일 완료 기반)**·개근 판정의 원천
-- ─────────────────────────────────────────────
CREATE TABLE daily_quiz_status (
	student_id CHAR(36) NOT NULL, 
	quiz_date DATE NOT NULL, 
	subject VARCHAR(20) NOT NULL, 
	topic VARCHAR(100), 
	status VARCHAR(20) NOT NULL, 
	reward_coins INTEGER NOT NULL, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(student_id) REFERENCES student_profiles (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_daily_quiz_status_quiz_date ON daily_quiz_status (quiz_date);
CREATE INDEX ix_daily_quiz_status_student_id ON daily_quiz_status (student_id);

-- ─────────────────────────────────────────────
-- ▶ family_messages  [학부모]
--   무엇: 교사→가정 안내 메시지
--   왜: 가정통신/알림장 기능 데이터
-- ─────────────────────────────────────────────
CREATE TABLE family_messages (
	organization_id CHAR(36) NOT NULL, 
	teacher_id CHAR(36) NOT NULL, 
	student_id CHAR(36) NOT NULL, 
	message TEXT NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	read_at DATETIME, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(teacher_id) REFERENCES users (id), 
	FOREIGN KEY(student_id) REFERENCES student_profiles (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_family_messages_organization_id ON family_messages (organization_id);
CREATE INDEX ix_family_messages_student_id ON family_messages (student_id);
CREATE INDEX ix_family_messages_teacher_id ON family_messages (teacher_id);

-- ─────────────────────────────────────────────
-- ▶ learning_attempts  [학습 기록]
--   무엇: 학습 시도 1건(과목·정오답·점수·풀이시간). replay(복습)는 보상 없음
--   왜: 모든 통계(정답률·주간그래프·성장)의 원천 데이터 — 가장 많이 쌓이는 테이블
-- ─────────────────────────────────────────────
CREATE TABLE learning_attempts (
	organization_id CHAR(36) NOT NULL, 
	student_id CHAR(36) NOT NULL, 
	subject VARCHAR(20) NOT NULL, 
	chapter_no INTEGER, 
	content_id CHAR(36), 
	result VARCHAR(20) NOT NULL, 
	score INTEGER NOT NULL, 
	solve_time_ms INTEGER NOT NULL, 
	retry_count INTEGER NOT NULL, 
	estimated_reason VARCHAR(50), 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(student_id) REFERENCES student_profiles (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_la_org_created ON learning_attempts (organization_id, created_at);
CREATE INDEX ix_la_student_created ON learning_attempts (student_id, created_at);
CREATE INDEX ix_learning_attempts_organization_id ON learning_attempts (organization_id);
CREATE INDEX ix_learning_attempts_student_id ON learning_attempts (student_id);
CREATE INDEX ix_learning_attempts_subject ON learning_attempts (subject);

-- ─────────────────────────────────────────────
-- ▶ learning_summaries  [학습 기록]
--   무엇: 기간별(주간 등) 학습 요약 집계 캐시
--   왜: 명단/리포트 화면에서 매번 원본을 집계하지 않도록 성능용
-- ─────────────────────────────────────────────
CREATE TABLE learning_summaries (
	organization_id CHAR(36) NOT NULL, 
	student_id CHAR(36) NOT NULL, 
	period_type VARCHAR(10) NOT NULL, 
	period_start DATE NOT NULL, 
	period_end DATE NOT NULL, 
	total_count INTEGER NOT NULL, 
	correct_count INTEGER NOT NULL, 
	average_solve_time_ms INTEGER NOT NULL, 
	streak_days INTEGER NOT NULL, 
	strength_tags JSON NOT NULL, 
	need_practice_tags JSON NOT NULL, 
	detail JSON NOT NULL, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(student_id) REFERENCES student_profiles (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_learning_summaries_organization_id ON learning_summaries (organization_id);
CREATE INDEX ix_learning_summaries_student_id ON learning_summaries (student_id);

-- ─────────────────────────────────────────────
-- ▶ parent_student_links  [학부모]
--   무엇: 학부모↔자녀 연결(승인 상태·일일목표·시간제한 설정 포함)
--   왜: 학부모가 볼 수 있는 자녀 범위를 정의. 초대코드로만 생성됨
-- ─────────────────────────────────────────────
CREATE TABLE parent_student_links (
	parent_user_id CHAR(36) NOT NULL, 
	student_id CHAR(36) NOT NULL, 
	organization_id CHAR(36) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	requested_at DATETIME, 
	approved_at DATETIME, 
	approved_by CHAR(36), 
	daily_goal INTEGER NOT NULL, 
	time_limit_enabled BOOL NOT NULL, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(parent_user_id) REFERENCES users (id), 
	FOREIGN KEY(student_id) REFERENCES student_profiles (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_parent_student_links_organization_id ON parent_student_links (organization_id);
CREATE INDEX ix_parent_student_links_parent_user_id ON parent_student_links (parent_user_id);
CREATE INDEX ix_parent_student_links_student_id ON parent_student_links (student_id);

-- ─────────────────────────────────────────────
-- ▶ recommendations  [학습 기록]
--   무엇: 취약점 기반 추천 문제
--   왜: 추천 문제 화면 데이터
-- ─────────────────────────────────────────────
CREATE TABLE recommendations (
	student_id CHAR(36) NOT NULL, 
	subject VARCHAR(20) NOT NULL, 
	chapter_no INTEGER NOT NULL, 
	priority VARCHAR(20) NOT NULL, 
	reason TEXT NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(student_id) REFERENCES student_profiles (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_recommendations_student_id ON recommendations (student_id);

-- ─────────────────────────────────────────────
-- ▶ student_badges  [보상·랭킹]
--   무엇: 학생별 배지 획득/진행률
--   왜: 배지 화면·개근상 지급 기록
-- ─────────────────────────────────────────────
CREATE TABLE student_badges (
	student_id CHAR(36) NOT NULL, 
	badge_id CHAR(36) NOT NULL, 
	earned_at DATETIME, 
	progress FLOAT NOT NULL, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(student_id) REFERENCES student_profiles (id), 
	FOREIGN KEY(badge_id) REFERENCES badges (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_student_badges_badge_id ON student_badges (badge_id);
CREATE INDEX ix_student_badges_student_id ON student_badges (student_id);

-- ─────────────────────────────────────────────
-- ▶ student_items  [보상·랭킹]
--   무엇: 학생이 구매한 아이템
--   왜: 보유 아이템/착용 상태
-- ─────────────────────────────────────────────
CREATE TABLE student_items (
	student_id CHAR(36) NOT NULL, 
	item_id CHAR(36) NOT NULL, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(student_id) REFERENCES student_profiles (id), 
	FOREIGN KEY(item_id) REFERENCES shop_items (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_student_items_item_id ON student_items (item_id);
CREATE INDEX ix_student_items_student_id ON student_items (student_id);

-- ─────────────────────────────────────────────
-- ▶ student_progress  [학습 기록]
--   무엇: 학생×과목 진도·누적 정답률
--   왜: 전체학습/진도 화면의 기준 값
-- ─────────────────────────────────────────────
CREATE TABLE student_progress (
	organization_id CHAR(36) NOT NULL, 
	student_id CHAR(36) NOT NULL, 
	subject VARCHAR(20) NOT NULL, 
	chapters_done INTEGER NOT NULL, 
	current_chapter INTEGER NOT NULL, 
	questions_done INTEGER NOT NULL, 
	accuracy FLOAT NOT NULL, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(student_id) REFERENCES student_profiles (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_student_progress_organization_id ON student_progress (organization_id);
CREATE INDEX ix_student_progress_student_id ON student_progress (student_id);
CREATE INDEX ix_student_progress_subject ON student_progress (subject);

-- ─────────────────────────────────────────────
-- ▶ wrong_answers  [학습 기록]
--   무엇: 오답 노트 항목
--   왜: 오답 다시풀기 화면 데이터
-- ─────────────────────────────────────────────
CREATE TABLE wrong_answers (
	student_id CHAR(36) NOT NULL, 
	organization_id CHAR(36) NOT NULL, 
	subject VARCHAR(20) NOT NULL, 
	category VARCHAR(30) NOT NULL, 
	question TEXT NOT NULL, 
	my_answer VARCHAR(200) NOT NULL, 
	correct_answer VARCHAR(200) NOT NULL, 
	tip TEXT, 
	reviewed BOOL NOT NULL, 
	wrong_date DATE, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(student_id) REFERENCES student_profiles (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_wrong_answers_organization_id ON wrong_answers (organization_id);
CREATE INDEX ix_wrong_answers_student_id ON wrong_answers (student_id);
CREATE INDEX ix_wrong_answers_subject ON wrong_answers (subject);

-- ============================================================
-- 끝. 이 스키마는 최신 백엔드 코드/마이그레이션과 동일합니다.
-- 백엔드에서 마이그레이션 이력을 맞추려면:
--   cd catchap-backend && alembic stamp head
-- 모델이 바뀌면 이 파일도 다시 생성해야 합니다 (담당: 태현).
-- ============================================================
