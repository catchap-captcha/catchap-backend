-- ============================================================
-- CatChap 캣챱 — DB 설치 스크립트 (MySQL 8.x)
-- 생성일: 2026-07-07 (SQLAlchemy 모델에서 자동 추출)
--
-- 사용법:
--   1) MySQL 8.x 설치 후 root(또는 관리자)로 접속
--   2) 아래 비밀번호(CHANGE_ME)를 팀 비밀번호로 바꾼 뒤 전체 실행:
--        mysql -u root -p < db-setup.sql
--   3) 백엔드 .env 의 DATABASE_URL 을 맞춰준다:
--        DATABASE_URL=mysql+pymysql://catchap:비밀번호@localhost:3306/catchap
--   4) (선택) 데모 데이터: catchap-backend 에서
--        python -m app.db.seed
--
-- 주의: 문자셋은 반드시 utf8mb4 (한글/이모지), 엔진 InnoDB.
-- ============================================================

CREATE DATABASE IF NOT EXISTS catchap
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'catchap'@'%' IDENTIFIED BY 'CHANGE_ME';
GRANT ALL PRIVILEGES ON catchap.* TO 'catchap'@'%';
FLUSH PRIVILEGES;

USE catchap;

-- ----- api_usage_logs -----
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

-- ----- audit_logs -----
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

-- ----- badges -----
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

-- ----- behavior_summaries -----
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
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_behavior_summaries_organization_id ON behavior_summaries (organization_id);
CREATE INDEX ix_behavior_summaries_student_id ON behavior_summaries (student_id);

-- ----- captcha_assets -----
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

-- ----- chapters -----
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

-- ----- contents -----
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

-- ----- email_logs -----
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

-- ----- email_verification_codes -----
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

-- ----- inquiries -----
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

-- ----- institutions -----
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

-- ----- invoices -----
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

-- ----- login_throttle -----
CREATE TABLE login_throttle (
	identifier VARCHAR(255) NOT NULL, 
	fail_count INTEGER NOT NULL, 
	id CHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	updated_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE UNIQUE INDEX ix_login_throttle_identifier ON login_throttle (identifier);

-- ----- model_versions -----
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

-- ----- notifications -----
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

-- ----- org_registration_requests -----
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

-- ----- organizations -----
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

-- ----- parent_invite_codes -----
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

-- ----- password_reset_tokens -----
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

-- ----- payment_methods -----
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

-- ----- plans -----
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

-- ----- refresh_tokens -----
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

-- ----- reports -----
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

-- ----- shop_items -----
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

-- ----- stat_blobs -----
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

-- ----- student_join_codes -----
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

-- ----- system_health_logs -----
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

-- ----- user_settings -----
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

-- ----- users -----
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

-- ----- ai_predictions -----
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

-- ----- captcha_settings -----
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

-- ----- classes -----
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

-- ----- inquiry_replies -----
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

-- ----- invitations -----
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

-- ----- memberships -----
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

-- ----- report_download_logs -----
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

-- ----- sites -----
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

-- ----- subscriptions -----
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

-- ----- api_keys -----
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

-- ----- student_profiles -----
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

-- ----- coin_transactions -----
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

-- ----- concept_reads -----
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

-- ----- daily_quiz_status -----
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

-- ----- family_messages -----
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

-- ----- learning_attempts -----
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

-- ----- learning_summaries -----
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

-- ----- parent_student_links -----
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

-- ----- recommendations -----
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

-- ----- student_badges -----
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

-- ----- student_items -----
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

-- ----- student_progress -----
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

-- ----- wrong_answers -----
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
-- Alembic 버전 스탬프 (이 스키마는 최신 마이그레이션과 동일)
-- 백엔드에서 마이그레이션 이력을 맞추려면 스크립트 실행 후:
--   cd catchap-backend && alembic stamp head
-- ============================================================
