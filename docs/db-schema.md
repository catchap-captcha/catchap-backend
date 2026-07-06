# CatChap MySQL DB 스키마

- MySQL 8.x / InnoDB / utf8mb4 (`utf8mb4_0900_ai_ci`)
- 모든 주요 테이블: `CHAR(36)` UUID PK, `created_at`/`updated_at`
- 멀티테넌트: `organization_id` 기준 분리 — API 단계에서 RBAC 검사 (`app/core/permissions.py`)
- 토큰/시크릿 **원문 저장 금지**: `code_hash`/`token_hash`/`secret_key_hash`(SHA-256)만 저장
- 학생 개인정보 최소화: 실명 대신 `nickname`, 가명 `student_code`(CAT-xxxx)

## 인증/계정

| 테이블 | 설명 | 주요 컬럼 |
| --- | --- | --- |
| `users` | 이메일 계정 (parent/teacher/org_admin/ops) | email(uq), password_hash(bcrypt), role, status, email_verified_at, two_factor_enabled, organization_id |
| `student_profiles` | 학생 (기관 선택+ID/PW 로그인) | organization_id FK, class_id FK, student_login_id, student_code(uq), password_hash, nickname, age, grade_band, avatar(JSON), coins, level, status |
| `email_verification_codes` | 6자리 이메일 인증 코드 (디자인: 코드 입력 방식) | email, purpose(signup/reset), code_hash, expires_at(5분), used_at(1회 사용), verified_at |
| `password_reset_tokens` | (예비) 링크 방식 재설정 토큰 | user_id, token_hash(uq), expires_at, used_at |
| `refresh_tokens` | refresh 회전 관리 | user_id, subject_type(user/student), token_hash(uq), expires_at, revoked_at |

이메일 인증 흐름: `POST /auth/email/send`(코드 발송, dry-run 지원) → `POST /auth/email/verify`(verified_at) → 가입/재설정 확정 시 `used_at` 처리(재사용 방지).

## 기관/멤버

| 테이블 | 설명 | 주요 컬럼 |
| --- | --- | --- |
| `organizations` | 고객 기관 | name, code(uq, HS-EDU-xxxx), org_type, status, business_number(uq), code_expires_at |
| `org_registration_requests` | 기관 가입 신청 (1차: 자동 승인) | org_name, contact_*, status(pending→approved), organization_id |
| `institutions` | InstitutionPicker 검색 디렉토리 | name, inst_type, sido/sigungu/dong(idx), road_address, organization_id |
| `memberships` | 기관 소속 (교사/관리자) | user_id FK(**nullable** — 교사 코드 선발급→가입 시 클레임), organization_id FK, role, status, teacher_code(uq, T-xxxx), position, career_years |
| `invitations` | 멤버 초대 | organization_id, email, role, token_hash(uq), expires_at, accepted_at |
| `classes` | 학급 | organization_id FK, name, grade, teacher_id FK, status |
| `parent_student_links` | 학부모-자녀 연결 (요청/승인 구조, 1차: 코드 입력 시 자동 승인) | parent_user_id FK, student_id FK, status(requested/approved/rejected/removed), requested_at, approved_at, daily_goal, time_limit_enabled |

## 학습/콘텐츠

| 테이블 | 설명 |
| --- | --- |
| `chapters` | 과목 6 × 챕터 5 정의 + 개념(JSON: summary/points/example) |
| `contents` | 콘텐츠/검색 인덱스 (category, subject, difficulty, route_hint) |
| `student_progress` | 과목별 진도 (chapters_done, current_chapter, accuracy) |
| `learning_attempts` | 학습 시도 (result, score, solve_time_ms, retry_count, estimated_reason) |
| `learning_summaries` | 기간 요약 (period_type/start/end, 통계 + detail JSON 시계열) |
| `wrong_answers` | 오답노트 (question, my_answer, correct_answer, tip, reviewed) |
| `recommendations` | 취약문제추천 (priority, reason) |
| `daily_quiz_status` | 오늘의퀴즈 과목별 상태 (todo/doing/done, reward_coins) |
| `concept_reads` | 개념설명 읽음 (서버 동기화) |
| `behavior_summaries` | 행동 요약 (path_length, pause_count, drop_distance_norm, interaction_result, risk_level low/review/elevated) |

## 보상/상점

| 테이블 | 설명 |
| --- | --- |
| `badges` / `student_badges` | 배지 12종 정의 / 획득·도전 진행률 |
| `shop_items` / `student_items` | 상점(모자·배경·스티커 24종) / 보유 |
| `coin_transactions` | 냥코인 적립/사용 이력 |

## CAPTCHA API/사이트 (백엔드 완전 구현, 프론트는 읽기전용 위젯만 — 디자인 기준)

| 테이블 | 설명 |
| --- | --- |
| `sites` | 연동 사이트 (domain, allowed_origins JSON) |
| `api_keys` | site_key(공개, uq) + secret_key_hash(발급 시 1회만 노출) |
| `api_usage_logs` | 호출 로그 (endpoint, status_code, latency_ms) |
| `captcha_settings` | 기관별 캡차 종류 on/off + 라운드당 개수 + 셔플 |
| `captcha_assets` / `ai_predictions` | 이미지 자산 + AI 태그 검수(승인/반려) — AI 추론은 catchap-ai-service stub |

## 알림/운영/기타

| 테이블 | 설명 |
| --- | --- |
| `notifications` | 역할별 알림 (user_id 또는 student_id, category, child_id, read_at) |
| `user_settings` | 역할별 설정 JSON (subject_type user/student) |
| `family_messages` | 가정안내: 교사→보호자 메시지 (status sent/read) |
| `inquiries` | 문의하기 접수 |
| `inquiry_replies` | 문의 답변 (1문의:N답변) — 운영자 답변→문의자 이메일 회신. inquiry_id FK, body, answered_by, email_status |
| `reports` / `report_download_logs` | 리포트 + 다운로드 감사 |
| `audit_logs` | 감사 로그 (action, target, before/after JSON) |
| `email_logs` | 발송 로그 (sent/failed/dry_run) |
| `system_health_logs` / `model_versions` | 운영 모니터링 / AI 모델 레지스트리 |
| `plans` / `subscriptions` / `payment_methods` / `invoices` | 요금제(조회 전용)/구독/결제수단/결제내역 — 결제 실행은 mock |

## 권한 분리 기준 (API 강제)
- **org_admin**: 자기 기관 전체 (`check_org_scope`)
- **teacher**: 담당 학급(`classes.teacher_id`) 학생만
- **parent**: `parent_student_links.status='approved'` 자녀만 (`check_parent_child`)
- **student**: 본인(`/students/me/*`)만
- **ops**: `/ops/*` 전용

## Migration
Alembic (`alembic/`) — `alembic upgrade head`로 전체 테이블 생성. seed: `python -m app.db.seed`.
