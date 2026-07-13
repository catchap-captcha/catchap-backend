# Backend 구현 체크리스트 (1차)

> 최신화 2026-07-06: 테이블 52개·마이그레이션 9개·테스트 39개. 기관 가입은 **자동 승인 → 운영자 승인(pending→active)**으로, 학부모 연결은 자동승인 → **rate-limit/초대코드 기반**으로 변경됨(온보딩 재설계 착수). 상세: [onboarding-security-design.md](./onboarding-security-design.md), [../../PROJECT_HANDOFF.md](../../PROJECT_HANDOFF.md).

## 구현 완료
- [x] JWT 인증 (access 30분 / refresh 14일 회전·revoke, token_hash만 저장)
- [x] 역할별 로그인 4종: 학부모/교사/기관 관리자/운영자 (이메일) + 학생 (기관 선택+ID/PW)
- [x] 회원가입 4종: 학부모, 교사(개별코드 T-xxxx 클레임), 학생(기관코드 검증+CAT-xxxx 발급), 기관(신청→자동 승인+HS-EDU-xxxx 코드+관리자 계정+구독)
- [x] 이메일 인증: 6자리 코드 방식(디자인 기준) — code_hash 저장, 5분 만료, 1회 사용, 재발송
- [x] 비밀번호 재설정: 이메일→코드→새 비밀번호 (+전 기기 로그아웃)
- [x] Gmail SMTP (.env 설정, 미설정 시 콘솔 dry-run) + email_logs + 디자인 스타일 HTML 템플릿 3종
- [x] RBAC (API 강제): org scope / 교사 담당 학급 / 학부모 승인 자녀 / 학생 본인 / ops
- [x] 학생 API 19종 (대시보드/진도/기록/오답/배지/추천/퀴즈/지갑/상점/아바타/랭킹/시도 저장/결과/게임 상태/개념 읽음/검색)
- [x] 학부모 API (자녀/요약/리포트/연결 요청·자동승인/해제/자녀 설정/리포트 다운로드 로그)
- [x] 교사 API (대시보드/우리반 CRUD/전교 조회/분석/가정안내/프로필/담당 학급)
- [x] 기관 API (대시보드/분석/학급·로스터/교사 CRUD(담임+학생>0 삭제 차단)/캡차설정/AI 모델/요금제·결제 내역 조회/관리자/사이트 상태)
- [x] 알림 (역할별 조회/읽음/전체읽음), 설정 (역할별 JSON, 비번 변경, 전 기기 로그아웃, 데이터 내보내기, 계정 비활성화)
- [x] site_key/secret_key: 발급 구조 + secret은 hash만 저장 (프론트는 디자인대로 읽기전용 위젯만)
- [x] 감사 로그 (교사 추가/삭제, 자녀 연결/해제, 설정/캡차설정 변경 등)
- [x] Alembic migration (52 tables, 마이그레이션 9개) + seed (멱등, `python -m app.db.seed`)
- [x] 테스트 39개: auth(로그인/역할 불일치/refresh 회전/가입/코드 만료·재사용), RBAC(무인증/역할 간/기관 간/자녀 연결), 집계, 학생 데이터, health

## stub 처리 (다음 단계에서 본격 구현)
- [x] 로그인 메인 CAPTCHA — `/captcha/forest/*` 문제·오브젝트별 포즈·검증·단일사용 토큰
- [ ] 구형 `GET /captcha/challenge` — 호환용 200 stub, 신규 연동은 `/captcha/forest/*` 사용
- [ ] 교육용 게임 로직 — 게임 상태/결과는 seed 기반 API
- [ ] AI 챗봇 2종 (`/ai/student-chat`, `/ai/parent-chat`) — 디자인 canned 응답 stub
- [ ] AI 이미지 분류 — catchap-ai-service stub 연동 지점만
- [ ] 운영자(OPS) API 5종 — 최소 stub (화면 없음)
- [ ] 결제 실행 — 요금제/내역 조회만, 결제는 mock
- [ ] 리포트 PDF 생성 — 다운로드 로그+stub URL

## 사용자 확인 필요
- Gmail SMTP 계정/앱 비밀번호 (.env `SMTP_USER`/`SMTP_APP_PASSWORD` — 채우면 실발송, 현재 dry-run)
- 클라우드 MySQL 전환 시점 (.env `DATABASE_URL` 교체만으로 전환)
- BINARY(16) UUID 최적화 여부 (현재 CHAR(36))

## 테스트 방법
```powershell
cd catchap-backend
.venv\Scripts\activate
$env:PYTHONIOENCODING='utf-8'
python -m pytest tests -q      # 단위/통합 (SQLite in-memory)
python -m uvicorn app.main:app --reload   # 서버 (MySQL 필요 — scripts\start-mysql.ps1)
```
개발 계정: docs/DEV_ACCOUNTS.md 참고 (admin/teacher/parent/ops@catchap.dev · Password123!, 학생 student01/1234 — **개발 전용, 운영 반입 금지**)
