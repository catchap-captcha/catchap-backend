# 소셜 로그인(카카오·네이버·구글) — 설계 (2026-08-06)

> **상태: 백엔드 구현 완료 · 프론트 미착수 · 운영 키 미발급.**
> 모델 `SocialAccount`(+ 마이그레이션 `social_login_01`) · provider 어댑터
> `services/social_auth.py` · 판정 `services/social_login_service.py` ·
> 라우터 `api/v1/endpoints/social_auth.py` · 탈퇴 시 연결 파기(`privacy_service`) ·
> 테스트 `tests/test_social_auth.py` 23건.
>
> 공부 키워드: **OAuth 2.0 authorization code grant**, **state(CSRF)**, **account linking**,
> **email verification trust**, **오픈 리다이렉트**.

## 1. 무엇을 만들었나

학생이 카카오·네이버·구글 계정으로 가입·로그인한다.

**소셜로 만들 수 있는 계정은 학생뿐이다.** 운영자·강사(콘솔 계정)는 소셜로 생성되지 않는다 —
권한을 자동으로 부여하지 않는다는 뜻이다.

콘솔 계정의 소셜 **로그인**은 2026-08-07에 열었다(social_login_02). 다만 자동이 아니다:
본인이 비밀번호로 로그인한 뒤 프로필에서 직접 연결한 경우에만 열린다(§3.1). 고권한 계정을
외부 IdP라는 공격면에 두지 않는다는 기존 방침(운영자 로그인 분리, 2026-07-20)은 그대로다 —
공격면이 넓어지는 지점이 '이메일 일치'가 아니라 '본인의 명시적 행위'로 좁혀졌을 뿐이다.

## 2. 흐름 — 왜 신규 가입이 2단계인가 ★

```
① GET  /auth/social/{provider}/authorize     → authorize_url + state(서명, 10분)
② 사용자가 provider 동의 화면에서 승인       → redirect_uri?code=..&state=..
③ POST /auth/social/{provider}/callback      → 아래 셋 중 하나
     logged_in       이미 연결된 소셜 계정
     logged_in       검증된 이메일이 기존 학생과 일치 → 그 계정에 연결
     signup_required 신규 — signup_token(15분)만 주고 계정은 아직 안 만든다
④ POST /auth/social/signup                   → 생년월일 확인 후 계정 생성 + 토큰
```

이 서비스는 **만 14세 미만이면 보호자(법정대리인) 동의 없이 가입할 수 없다**
(`auth_service.GUARDIAN_CONSENT_AGE`). 그런데 provider는 생년월일을 안 줄 수 있다 —
구글 userinfo에는 아예 없고, 카카오·네이버는 선택 동의 항목이다.

콜백에서 바로 계정을 만들면 **생년월일 없는 계정이 생겨 연령 게이트가 조용히 우회된다.**
그래서 콜백은 서명 토큰만 주고, 생년월일을 받은 뒤에 계정을 만든다. 덕분에

- 반쯤 만들어진 계정이 생기지 않고,
- "일단 만들고 나중에 지우는" 보정 로직도 필요 없다.

provider가 생년월일을 준 경우 그 값이 정본이다 — 사용자가 요청 본문에 다른 생일을 실어도
서버는 토큰 안의 값을 쓴다(테스트로 고정).

## 3. 계정 매핑 규칙

| 상황 | 처리 |
| --- | --- |
| `(provider, provider_user_id)` 연결 있음 | 그 학생으로 로그인 |
| 연결 없음 + **검증된** 이메일이 기존 학생과 일치 | 자동 연결 후 로그인 |
| 연결 없음 + **미검증** 이메일이 기존 학생과 일치 | **409** — 기존 방식으로 로그인한 뒤 수동 연결 |
| `(provider, provider_user_id)`가 콘솔 계정에 연결됨 | 그 콘솔 계정으로 로그인(§3.1) |
| 이메일이 콘솔 계정(users)과 일치 + 연결 없음 | **400** — 연결 방법을 안내(자동 연결 없음) |
| 그 외 | `signup_required` |

**미검증 이메일을 자동 연결하지 않는 이유**가 이 기능의 가장 중요한 보안 결정이다.
provider가 "이 이메일의 소유자가 맞다"고 확인해 주지 않은 상태에서 이메일만 같다고 붙이면,
공격자가 남의 이메일을 자기 소셜 계정에 등록해 **그 사람의 계정을 통째로 가져갈 수 있다.**

- 카카오: `is_email_valid` **AND** `is_email_verified` 둘 다 참일 때만 검증으로 본다.
- 구글: `email_verified` 값을 그대로 쓴다.
- 네이버: 응답에 검증 필드가 없다. 네이버 계정은 가입 시 이메일 본인확인을 거치므로
  검증으로 간주한다(`NaverProvider.email_verified_default`). 정책을 조이려면 이 값을
  False로 바꾸면 되고, 그러면 네이버 사용자는 항상 신규 가입 또는 수동 연결을 거친다.

### 3.1 콘솔 계정 수동 연결 ★

`social_accounts`는 `student_id`와 `user_id` 중 **정확히 하나**만 채운다(주체 판별).
이 불변식은 `_create_link()` 한 곳에서 강제한다 — 이 테이블에 쓰는 경로가 거기뿐이라
DB CHECK 제약 없이도 깨지지 않는다.

```
① 콘솔 계정으로 비밀번호 로그인 (= 본인 확인)
② /ops/profile → '간편 로그인 연결' → 연결
③ 이후 /ops/login 의 소셜 버튼 한 번으로 로그인
```

②를 거치지 않으면 이메일이 같아도 400이다. **이메일 일치를 근거로 삼지 않는 이유**는
학생 쪽 미검증 이메일과 같다(아래) — 다만 콘솔 계정은 피해가 권한 탈취로 이어지므로,
검증된 이메일이어도 자동 연결하지 않는다. 즉 학생보다 한 칸 더 조인 규칙이다.

연결 행이 곧 '동의의 증거'이므로, 사고 조사 때 `user_id`가 채워진 행은 그 계정 소유자가
로그인 상태에서 직접 눌렀다는 뜻으로 읽으면 된다.

로그인 시에는 `user.status == "disabled"`를 확인하고 403으로 막는다 — 정지된 콘솔 계정이
소셜 경로로 우회 입장하지 못하게. 토큰은 그 계정의 실제 역할로 발급하고
응답의 `student`는 `null`이다.

## 4. 보안 장치

- **state**: 서버 비밀키로 서명한 JWT(10분). `provider`와 `redirect_uri`를 담아 콜백에서
  교차 사용(구글 state로 카카오 콜백)과 주소 바꿔치기를 막는다. 별도 저장소를 두지 않은
  것은 인가 코드 자체가 1회용이라 state만 재사용해도 얻을 게 없기 때문이다.
- **redirect_uri 허용목록**(`SOCIAL_REDIRECT_URIS`): 목록 밖 주소는 400. 없으면 공격자가
  자기 사이트로 인가 코드를 흘릴 수 있다(오픈 리다이렉트). 어떤 주소가 허용인지는
  응답으로 알려 주지 않는다.
- **access token 미저장**: provider 토큰은 프로필 1회 조회에만 쓰고 버린다. 우리는 소셜
  계정의 API를 대신 호출하지 않으므로 보관할 이유가 없다 — 유출 표면도 갱신 부담도 없앤다.
  그래서 `social_accounts`에 토큰 컬럼이 없다.
- **DB 유일 제약 2개**: `(provider, provider_user_id)`로 한 소셜 계정이 두 학생에 붙는 것을,
  `(student_id, provider)`로 한 학생이 같은 provider를 중복 연결하는 것을 막는다.
- **비밀번호 로그인 차단**: 소셜 전용 계정의 `password_hash`는 bcrypt 형식이 아닌 자리값
  (`!social-login-only`)이라 `verify_password`가 항상 False다.
- **마지막 로그인 수단 보호**: 비밀번호가 없고 연결이 하나뿐이면 연결 해제를 400으로 막는다.
  끊는 순간 계정에 다시 들어올 수 없기 때문이다.
- **레이트리밋**: 콜백은 IP당 시간 60회(`RATE_CALLBACK_PER_HOUR`).
- **가짜 성공 금지**: 키가 없는 provider는 목록에서 `enabled=false`이고 호출하면 503이다.
  버튼만 동작하는 척하지 않는다.

## 5. API

| Method | Path | 인증 | 설명 |
| --- | --- | --- | --- |
| GET | `/api/v1/auth/social/providers` | — | 설정된 provider 목록(버튼 노출 판단) |
| GET | `/api/v1/auth/social/{provider}/authorize` | — | 동의 화면 URL + state |
| POST | `/api/v1/auth/social/{provider}/callback` | — | code·state → 로그인 / signup_required |
| POST | `/api/v1/auth/social/signup` | — | signup_token + 생년월일 → 계정 생성 |
| GET | `/api/v1/auth/social/connections` | 학생·콘솔 | 내 연결 목록 + has_password |
| POST | `/api/v1/auth/social/{provider}/connect` | 학생·콘솔 | 로그인 상태에서 추가 연결 |
| DELETE | `/api/v1/auth/social/{provider}` | 학생·콘솔 | 연결 해제 |

관리 3종은 `get_current_principal`을 쓴다 — 학생이든 콘솔이든 '로그인한 본인'이면 된다.
콘솔 계정은 항상 비밀번호가 있어 `has_password`가 true이고, 마지막 연결도 해제할 수 있다.

콜백 응답 예:

```jsonc
// 신규
{"status":"signup_required","provider":"kakao","signup_token":"eyJ…",
 "profile":{"email":"a@b.dev","nickname":"하은","birth_date":null,"needs_birth_date":true}}
// 로그인
{"status":"logged_in","provider":"kakao","linked_now":false,
 "tokens":{"access_token":"…","refresh_token":"…","token_type":"bearer"},
 "student":{"id":"…","nickname":"하은","student_code":"CAT-AB12CD"}}
```

## 6. 프론트가 할 일

1. 로그인 화면 진입 시 `GET /auth/social/providers` → `enabled=true`인 버튼만 그린다.
2. 버튼 클릭 → `GET /auth/social/{provider}/authorize` → 받은 `authorize_url`로 이동
   (`window.location.href`). state를 따로 보관할 필요는 없다(서버가 서명으로 검증한다).
3. `/auth/social/callback` 라우트에서 쿼리의 `code`·`state`를 그대로
   `POST /auth/social/{provider}/callback`에 전달.
4. `logged_in`이면 토큰 저장 후 홈으로. `signup_required`면 약관 동의 + (필요 시) 생년월일
   입력 화면을 띄우고 `POST /auth/social/signup` 호출.
5. 계정 설정 화면에 `GET /auth/social/connections` 목록과 연결/해제 버튼.
   `has_password=false`이면 "비밀번호 설정" 안내를 함께 노출한다.
6. **콘솔도 같은 컴포넌트를 쓴다.** 학생 마이페이지와 `/ops/profile`이
   `components/account/SocialConnections`를 공유하고, 콜백 화면은 하나뿐이므로 '연결 후
   돌아갈 경로'를 출발할 때 sessionStorage에 남긴다(`rememberSocialIntent(..., returnTo)`).
   그 값은 앱 내부 경로만 통과시킨다 — 저장소가 오염되면 오픈 리다이렉트가 되기 때문.

## 7. 운영 준비 체크리스트

- [x] **카카오 (2026-08-06 완료)**: 앱 `CATCHAP`(ID 1535749) · 카카오 로그인 ON ·
      리다이렉트 URI 2개(운영 www.catchap5.com, 로컬 localhost:5173) · 동의항목 닉네임=필수.
      키는 새 콘솔에서 **앱 설정 → 플랫폼 키 → REST API 키**에 있고, 리다이렉트 URI도
      그 키의 [수정] 안에 있다(예전 '플랫폼/카카오 로그인' 위치가 아니다).
      ★**이메일(account_email)은 '권한 없음'** — 카카오는 비즈 앱에만 이메일을 연다.
      그래서 `KAKAO_SCOPES` 기본값이 `profile_nickname`이고, 카카오 가입자는 이메일 없이
      `kakao_{uid}` 아이디로 만들어진다(기존 계정 자동 연결도 이메일이 없어 동작하지 않는다).
      비즈 앱 전환(사업자 정보 또는 개인 개발자 본인인증) 후 동의항목을 열고
      `KAKAO_SCOPES=profile_nickname account_email` 로 바꾸면 둘 다 살아난다.
      ★클라이언트 시크릿이 **활성화 ON** 상태라 `KAKAO_CLIENT_SECRET`이 **필수**다.
- [ ] 네이버 개발자센터: 애플리케이션 등록 → 로그인 오픈 API → Callback URL 등록 →
      Client ID/Secret. 회원이름·이메일·생일 항목 사용 신청.
- [ ] GCP: OAuth 동의 화면 구성 → 사용자 인증 정보 → OAuth 클라이언트 ID(웹) →
      승인된 리디렉션 URI 등록 → Client ID/Secret.
- [ ] 세 콘솔의 Redirect URI와 `SOCIAL_REDIRECT_URIS`가 **문자 단위로 일치**해야 한다
      (슬래시 하나만 달라도 provider가 거절한다).
- [ ] 배포 후 `alembic upgrade heads`로 `social_accounts` 생성.
      (주의: 이 저장소는 현재 head가 2개다 — `lecture_report_01`과 `social_login_01`.
      `upgrade head`는 실패하므로 `heads`를 쓰거나 머지 리비전을 하나 만들어야 한다.)

## 8. 남은 일 / 다음 방향

- **콘솔 계정 연결의 감사 로그.** 지금은 행의 존재가 증거지만, 연결·해제 시점을 감사
  로그(audit_logs)에도 남기면 운영자 권한 변화 추적과 한 화면에서 본다.

- **소셜 전용 계정의 비밀번호 설정 경로.** 지금은 이메일이 로그인 아이디인 계정만
  기존 학생 비밀번호 재설정(`/auth/student-password-reset`)으로 비밀번호를 만들 수 있다.
  이메일 없이 가입한 카카오 사용자(`kakao_1234`)는 그 경로가 없다 — 연결이 하나뿐이면
  해제가 막히므로 잠기지는 않지만, "비밀번호 설정" 전용 엔드포인트를 두는 편이 낫다.
- **애플 로그인**: iOS 앱을 낸다면 심사 규정상 사실상 필수다. 어댑터 한 개 추가로 붙는다
  (다만 애플은 이메일 릴레이·이름 1회 제공이라 프로필 처리 분기가 필요).
