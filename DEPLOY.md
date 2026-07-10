# CatChap 배포 가이드 (4대 서버 · Docker · catchap5.com)

## 0. 구성 개요

| 서버 | 공인 IP | 사설 IP | 역할 | 주소 |
|---|---|---|---|---|
| DB | 210.109.52.114 | 10.0.1.168 | MySQL 8 (기존) | 사설 3306 |
| 백엔드 | 210.109.52.124 | 10.0.1.73 | FastAPI + Caddy | https://api.catchap5.com |
| 프론트 | 210.109.14.25 | 10.0.1.84 | React(nginx) + Caddy | https://www.catchap5.com |
| AI | 210.109.53.16 | 10.0.1.198 | (보류 — 현재 stub, 백엔드 미연동) | — |

- TLS는 **Caddy가 Let's Encrypt로 자동 발급/갱신**. 프론트·백엔드 둘 다 https라 혼합콘텐츠 없음.
- **빌드 소스 브랜치 고정**: 백엔드=`worktree-widget-orig`, 프론트=`worktree-mobile-fe`. (메인/th에 병합돼 있으면 th 사용 가능 — 단 forest_captcha·app/static 자산·최신 마이그레이션이 반드시 포함돼야 함.)

## 1. DNS (도메인 등록업체에서 A 레코드)

```
www.catchap5.com   A   210.109.14.25     # 프론트
catchap5.com       A   210.109.14.25     # apex → www 리다이렉트
api.catchap5.com   A   210.109.52.124    # 백엔드 API
```
Caddy가 인증서를 발급하려면 이 A 레코드가 먼저 전파돼 있어야 한다(80/443 인바운드 열림 필수).

## 2. 방화벽/보안그룹 (열어야 할 포트)

- 프론트 VM: 80, 443 (전세계)
- 백엔드 VM: 80, 443 (전세계 — 브라우저가 직접 API 호출)
- DB VM: 3306 은 **사설망(10.0.1.0/24)만** 허용 권장.
  - ⚠️ 지금은 팀원 로컬 개발을 위해 **공인 3306을 닫지 않는다**(추후 선택적으로). 배포 후에도 유지.
- 백엔드→DB 는 사설 IP `10.0.1.168:3306` 사용(`.env.production`).

## 3. DB 준비 (배포 전 1회)

클라우드 DB(`catchap_dev_db`)는 스키마 57테이블 완비 · 데이터 0.

1. **alembic 마커 정렬** — 실제 스키마는 최신(코드와 56테이블/컬럼 일치, `invitations.teacher_code` 존재)인데 `alembic_version` 만 1리비전 뒤(`c4d5e6f7a8b9`). 마커만 head로 맞춘다(스키마 변경 아님 → DML, `catchap_backend` 로 가능):
   ```bash
   # 백엔드 코드 기준, DATABASE_URL 을 클라우드로 두고
   alembic stamp head    # head = d0e1f2a3b4c5
   ```
   - ⚠️ `alembic upgrade head` 는 쓰지 말 것 — `teacher_code` 가 이미 있어 실패한다.
   - **앞으로 스키마를 바꾸는 마이그레이션**은 DDL이라 `catchap_backend`(DML 전용) 불가 → 그때는 `catchap_dba` 자격으로 `alembic upgrade head` 실행.

2. **시드** — 운영자(OPS)·플랜·기본 데이터 (DML이라 `catchap_backend` 가능):
   ```bash
   python -m app.db.seed        # (프로젝트 시드 스크립트 — 실제 명령은 seed.py 확인)
   ```

3. **교육형 API 키 발급** — OPS 콘솔(운영자 로그인) → API 키 → 교육형(edu) 키 발급.
   발급된 `ck_edu_...` 를 **프론트 빌드 변수**(`VITE_CATCHAP_EDU_SITE_KEY`)에 넣는다.
   (로컬에서 만든 키는 클라우드 DB에 없어 위젯 인증 실패.)

## 4. 백엔드 배포 (백엔드 VM)

```bash
# Docker 설치 후
git clone -b worktree-widget-orig https://github.com/catchap-captcha/catchap-backend.git
cd catchap-backend

cp .env.production.example .env.production
#  .env.production 편집:
#   - DATABASE_URL 의 <DB_PW_URLENCODED> 채우기 (@ → %40), 호스트 10.0.1.168
#   - JWT_SECRET_KEY 신규 생성: python -c "import secrets;print(secrets.token_hex(32))"
#   - SMTP_USER / SMTP_APP_PASSWORD
#   (ENV=production, CORS/FRONTEND/BACKEND URL 은 이미 도메인으로 채워져 있음)

docker compose up -d --build
curl -fsS https://api.catchap5.com/health     # {"status":"ok"} 확인
```
- `ENV=production` 이면 부팅 시 JWT 32자·CORS 비와일드카드·SMTP 설정을 강제 검증한다(미충족 시 기동 거부 = 정상 안전장치).

## 5. 프론트 배포 (프론트 VM)

```bash
git clone -b worktree-mobile-fe https://github.com/catchap-captcha/catchap-frontend.git
cd catchap-frontend

cp .env.production.example .env
#  .env 편집: VITE_CATCHAP_EDU_SITE_KEY = 3단계에서 발급한 클라우드 edu 키
#            VITE_API_BASE_URL = https://api.catchap5.com (기본값)

docker compose --env-file .env up -d --build
```
- 빌드 타임에 API 주소·키가 번들에 인라인된다. 값 변경 시 `--build` 로 재빌드.

## 6. AI 서버

현재 `catchap-ai-service` 는 health/assets만 있는 **stub**이고, 백엔드가 AI를 호출하는 코드가 없다.
→ 지금은 배포 보류. 실제 행동AI 연동(백엔드→AI 호출 배선 + 모델)이 준비되면 그때 배포.

## 7. 배포 후 점검

- [ ] `https://api.catchap5.com/health` → 200
- [ ] `https://www.catchap5.com` 접속 → 로그인 화면
- [ ] 운영자/학생 로그인 → 게임 화면 위젯 렌더(edu 키 인증 OK)
- [ ] 브라우저 콘솔에 CORS/mixed-content 에러 없음
- [ ] 교사 초대 메일 발송 시 링크가 `https://www.catchap5.com/invite?...` (localhost 아님)

## 8. 남은 결정 (코드 밖)

- **아동 PII 보호자 동의 흐름**: 현재 미구현. 아동 실명·성별·학습기록 수집 전 법적 동의 절차 필요 — 서비스 오픈 전 결정.
- **시크릿**: 프로덕션 `JWT_SECRET_KEY` 는 개발 PC 것 재사용 금지(신규 생성). SMTP는 서비스 계정 권장. 발신 도메인(catchap5.com) SPF/DKIM 정렬로 메일 반송 방지.
- `.env*` 는 커밋 금지(`.gitignore`·`.dockerignore` 로 이미 제외).
