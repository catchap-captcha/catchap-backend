from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 프로덕션에서 절대 쓰면 안 되는 개발용 기본값
_INSECURE_JWT_DEFAULT = "dev-only-secret-change-me"

# .env는 실행 디렉터리와 무관하게 절대경로로 로드한다. (config.py = catchap-backend/app/core/,
# parents[2] = catchap-backend/) — 서버를 다른 폴더에서 띄워도 SMTP 등 설정이 비지 않게.
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore"
    )

    # Database
    DATABASE_URL: str = (
        "mysql+pymysql://catchap_user:catchap_pass_2026@localhost:3306/catchap?charset=utf8mb4"
    )

    # JWT
    JWT_SECRET_KEY: str = "dev-only-secret-change-me"
    # 에러 트래킹(Sentry) — 값이 있으면 활성, 비면 no-op. 아동 PII는 send_default_pii=False +
    # before_send 스크러빙으로 제외한다(main.py). .env.production에서 주입.
    SENTRY_DSN: str = ""
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14

    # Gmail SMTP (비어 있으면 콘솔 dry-run)
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_APP_PASSWORD: str = ""
    MAIL_FROM: str = ""
    MAIL_FROM_NAME: str = "CatChap"
    # 회신 주소(Reply-To). 비우면 헤더를 안 붙임. 발신전용 처리 시 no-reply 주소를 넣는다.
    MAIL_REPLY_TO: str = ""

    # URLs / CORS
    FRONTEND_URL: str = "http://localhost:5173"
    BACKEND_URL: str = "http://localhost:8000"
    CORS_ORIGINS: str = "http://localhost:5173"

    # 봇 판별 캡차 승급 — 시청 중 이상행동이 누적되면 메인 캡차(드래그)를 띄운다.
    # 인강 체크포인트 캡차('이 대목 봤는가')와는 별개 장치다: 뜨는 이유가 다르다.
    #   off     기존과 동일. 누적도 판정도 하지 않는다
    #   record  누적·판정은 하되 화면에는 아무것도 띄우지 않는다(응답에 플래그 미포함).
    #           임계값을 실트래픽으로 교정하는 단계 — 사용자 영향 0
    #   enforce 임계 초과 시 응답에 captcha_required 를 실어 보낸다
    BOT_ESCALATION_MODE: str = "off"
    # 임계값. record 모드로 관측한 뒤 정하는 값 — 아래 기본값은 근거 없는 출발점이다.
    BOT_SUSPICION_THRESHOLD: int = 10
    # 메인 캡차 호스트. 비우면 승급이 동작하지 않는다(빈 값이면 off 로 강등).
    MAIN_CAPTCHA_URL: str = ""
    # /api/verify-token 서버검증용. 절대 프런트로 노출하지 않는다.
    MAIN_CAPTCHA_SITE_SECRET: str = ""

    # 강의 시청 검증 — 영상 저장 디렉터리(경로는 DB에 저장하지 않고 {id}{ext}로 유도)와
    # 업로드 상한. 전역 1MB 본문 제한의 예외 처리는 main.py 미들웨어가 담당한다.
    LECTURE_MEDIA_DIR: str = "./media/lectures"
    MAX_UPLOAD_BYTES: int = 5 * 1024 * 1024 * 1024  # 5GB (2026-07-22 상향)
    # 강의 자료(자료실) 파일 상한 — 영상 상한(5GB)과 분리. 자료는 문서류(pdf/pptx/zip 등)라
    # 50MB면 충분하고, 영상 상한을 그대로 열면 자료 경로가 대용량 업로드 표면(디스크 소모)이 된다.
    MAX_MATERIAL_UPLOAD_BYTES: int = 50_000_000
    # 확인 문항 이미지(강의 화면 캡처) 상한 — 캡처 PNG가 보통 1~3MB라 5MB면 충분하다.
    # 자료 상한(50MB)을 그대로 열면 문항 경로가 또 하나의 대용량 업로드 표면이 된다.
    MAX_QUESTION_IMAGE_BYTES: int = 5_000_000

    # 미디어 저장 위치 — local(서버 디스크) | object(Object Storage 버킷)
    # 왜 필요한가: 쿠버네티스에서 파드를 2개 이상 띄우면 파드마다 로컬 디스크가 달라
    # A 파드로 올린 영상을 B 파드가 못 찾는다(요청이 어디로 가느냐에 따라 404).
    # 버킷은 파드 밖에 있으므로 파드가 몇 개든 같은 파일을 본다.
    # ★기본값을 local 로 둬서 개발·테스트와 기존 배포는 동작이 전혀 바뀌지 않는다.
    #   문제가 생기면 이 값 하나만 되돌리면 된다(이미지 재빌드 불필요).
    MEDIA_STORAGE_BACKEND: str = "local"
    MEDIA_BUCKET: str = ""
    MEDIA_KEY_PREFIX: str = "media"  # 버킷 안 최상위 접두사. ★stt-temp/ 와 겹치지 않게 할 것
    MEDIA_S3_ENDPOINT: str = "https://objectstorage.kr-central-2.kakaocloud.com"
    MEDIA_S3_REGION: str = "kr-central-2"
    MEDIA_S3_ACCESS_KEY: str = ""
    MEDIA_S3_SECRET_KEY: str = ""

    # LLM 문항 자동 생성(Anthropic Messages API). 키가 비면 생성 기능은 503으로 정직하게
    # 거절한다 — stub 문제를 만들어 성공처럼 반환하지 않는다(가짜 성공 금지).
    ANTHROPIC_API_KEY: str = ""
    # STT(OpenAI Whisper) — 강의 음성 전사. 운영 콘솔 입력(DB)이 우선이고 이건 폴백.
    OPENAI_API_KEY: str = ""
    # 자체 호스팅 STT 워커(faster-whisper on GPU) — 설정되면 OpenAI 대신 이 워커로 전사한다
    # (과금 0·25MB 한계 없음·오디오 사내 보관). 비면 OpenAI 경로로 폴백(하위호환). stt-worker/ 참고.
    STT_WORKER_URL: str = ""
    STT_WORKER_TOKEN: str = ""  # 워커 인증 공유 시크릿(X-Worker-Token)
    # 각 VM의 메트릭 에이전트가 POST /internal/metrics 할 때 쓰는 공유 시크릿(X-Metrics-Token).
    # 비면 인제스트 비활성(백엔드 self-collect·시드만) — 배포 시 설정.
    METRICS_INGEST_TOKEN: str = ""
    # 클러스터 지표를 읽어올 프로메테우스(쿠버네티스 배포에서만 씀). 비면 노드·파드 수집을
    # 건너뛴다 — 로컬·VM 배포는 종전대로 에이전트 push만으로 동작한다(하위호환).
    # 비밀이 아니다(클러스터 내부 주소라 밖에서 닿지 않는다) → ConfigMap에 둔다.
    PROMETHEUS_URL: str = ""
    # 클러스터 지표를 몇 초마다 걷을지. ⚠️짧게 잡을수록 server_metric_samples가 빨리 쌓인다
    # (48시간 보존 × 서버 수). 30초면 서버당 5,760행/일 — 종전 에이전트 주기와 같다.
    CLUSTER_METRICS_INTERVAL_SEC: int = 30
    LLM_MODEL: str = "claude-opus-4-8"

    # 코스 수강 결제. PG 비밀 키는 서버에서만 사용하고 프런트로 내보내지 않는다.
    # mock은 개발 환경에서만 허용한다. ENV=production이면 PAYMENT_MOCK_ENABLED=true여도
    # 자동으로 비활성화되어, 키 누락이 실제 결제 성공으로 둔갑하지 않는다.
    PAYMENT_MOCK_ENABLED: bool = True
    TOSS_CLIENT_KEY: str = ""  # 프런트 결제창 초기화용 공개 키(응답으로 프런트에 전달 가능)
    TOSS_SECRET_KEY: str = ""  # 서버 결제 승인 검증용 비밀 키 — 절대 프런트로 노출하지 않는다
    KAKAOPAY_CID: str = ""  # 가맹점 코드(CID)
    KAKAOPAY_SECRET_KEY: str = ""  # 온라인 결제 Secret key — 절대 프런트로 노출하지 않는다
    KAKAOPAY_CID_SECRET: str = ""  # 계약에 따라 발급되는 CID 인증키(선택)
    # 포트원(PortOne) V2 — 여러 PG를 한 연동으로 묶는 중개 레이어.
    # store id·channel key는 브라우저 SDK 초기화에 쓰이므로 프런트로 내려도 되는 공개값이고,
    # API Secret과 웹훅 시크릿은 서버 전용이다.
    PORTONE_STORE_ID: str = ""  # store-xxxxxxxx (공개)
    PORTONE_CHANNEL_KEY: str = ""  # channel-key-xxxxxxxx (공개) — 콘솔 결제연동>채널관리에서 발급
    PORTONE_API_SECRET: str = ""  # V2 API Secret — 절대 프런트로 노출하지 않는다
    PORTONE_WEBHOOK_SECRET: str = ""  # 웹훅 서명 검증용(선택)
    # 비우면 FRONTEND_URL 아래 /student/payment/{success|fail|cancel}을 사용한다.
    PAYMENT_SUCCESS_URL: str = ""
    PAYMENT_FAIL_URL: str = ""
    PAYMENT_CANCEL_URL: str = ""

    # 소셜 로그인(카카오·네이버·구글) — 학생 계정 전용, 인가 코드 그랜트.
    # client_id는 authorize URL에 들어가는 공개값이고, client_secret은 서버 전용이다
    # (토큰 교환에만 쓰고 응답에 절대 싣지 않는다). 구글은 secret이 필수, 카카오는
    # '보안' 설정에서 켠 경우에만 필요, 네이버는 필수다.
    # 값이 비면 그 provider는 자동으로 비활성(버튼 미노출 + 호출 시 503)이다 —
    # 키 없이 동작하는 척하지 않는다(가짜 성공 금지).
    KAKAO_CLIENT_ID: str = ""
    KAKAO_CLIENT_SECRET: str = ""
    NAVER_CLIENT_ID: str = ""
    NAVER_CLIENT_SECRET: str = ""
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    # 콜백을 받을 프론트 주소 허용목록(쉼표 구분). 여기 없는 redirect_uri는 400으로 거절한다
    # — 없으면 공격자가 자기 사이트로 인가 코드를 흘릴 수 있다(오픈 리다이렉트).
    # 비우면 FRONTEND_URL/auth/social/callback 하나만 허용한다.
    # ★provider 콘솔(카카오 개발자·네이버 개발자센터·GCP)에 등록한 값과 반드시 일치해야 한다.
    SOCIAL_REDIRECT_URIS: str = ""

    @property
    def toss_enabled(self) -> bool:
        """실제 토스 결제 경로 활성 여부 — 두 키가 모두 있어야 승인 검증이 가능하다."""
        return bool(self.TOSS_CLIENT_KEY.strip()) and bool(self.TOSS_SECRET_KEY.strip())

    @property
    def kakaopay_enabled(self) -> bool:
        """카카오페이 ready/approve 호출에 필요한 CID와 Secret key가 모두 있는지."""
        return bool(self.KAKAOPAY_CID.strip()) and bool(self.KAKAOPAY_SECRET_KEY.strip())

    @property
    def portone_enabled(self) -> bool:
        """포트원 결제 경로 활성 여부.

        SDK 호출에 store id·channel key가, 서버 검증에 API Secret이 모두 필요하다.
        하나라도 비면 결제창을 띄워도 승인 검증을 못 하므로 아예 켜지 않는다."""
        return (
            bool(self.PORTONE_STORE_ID.strip())
            and bool(self.PORTONE_CHANNEL_KEY.strip())
            and bool(self.PORTONE_API_SECRET.strip())
        )

    @property
    def social_providers_enabled(self) -> list[str]:
        """설정이 끝난 소셜 provider 키 목록 — client_id가 있으면 사용 가능으로 본다."""
        return [
            p
            for p in ("kakao", "naver", "google")
            if (getattr(self, f"{p.upper()}_CLIENT_ID", "") or "").strip()
        ]

    @property
    def payment_mock_enabled(self) -> bool:
        return bool(self.PAYMENT_MOCK_ENABLED) and not self.is_production

    @property
    def payment_success_url(self) -> str:
        return self.PAYMENT_SUCCESS_URL.strip() or (
            f"{self.FRONTEND_URL.rstrip('/')}/student/payment/success"
        )

    @property
    def payment_fail_url(self) -> str:
        return self.PAYMENT_FAIL_URL.strip() or (
            f"{self.FRONTEND_URL.rstrip('/')}/student/payment/fail"
        )

    @property
    def payment_cancel_url(self) -> str:
        return self.PAYMENT_CANCEL_URL.strip() or (
            f"{self.FRONTEND_URL.rstrip('/')}/student/payment/cancel"
        )

    # 메인 캡차(사람 확인) — ms의 '다중 객체 드래그' 캡차를 우리 백엔드로 자체 이식.
    # 플래그가 켜지면 로그인/회원가입 5회 실패 스텝업이 forest 대신 드래그 캡차를 쓴다.
    # 기본 OFF — 승인 문제 데이터(captcha_questions/objects 행 + media/captcha 이미지)와
    # 프론트 위젯이 배치된 뒤 켠다. 자체 완결이라 외부 ms 서비스·GPU 도달 필요 없음.
    DRAG_CAPTCHA_ENABLED: bool = False
    # 승인 문제 이미지 루트 — 하위에 final/images, final/pieces (ms data/final 구조 그대로).
    CAPTCHA_MEDIA_DIR: str = "./media/captcha"
    CAPTCHA_CHALLENGE_TTL_SECONDS: int = 180
    CAPTCHA_VERIFICATION_TTL_SECONDS: int = 300
    CAPTCHA_MAX_ATTEMPTS: int = 3
    CAPTCHA_MAX_CHALLENGES_PER_MINUTE: int = 30
    # 서버가 잰 최소 풀이 시간(ms) — 챌린지 발급~제출을 서버가 재고(now-created_at), 이보다 빠르면
    # 위험 점수를 더한다. 클라이언트가 보내는 solve_time과 달리 봇이 못 속인다(감사: solve_time 서버
    # 계산). 0=비활성(기본) — 서버 측정값은 네트워크·이미지 로드를 포함해 실측으로 임계를 보정한 뒤
    # 켠다(예: 800). 켜기 전에도 서버 측정값은 항상 기록·요약에 남는다.
    CAPTCHA_MIN_SOLVE_MS: int = 0
    # 행동 위험 점수 임계 — 이상이면 통과 대신 재확인/차단(사람도 가끔 걸릴 수 있어 보수적).
    CAPTCHA_STEP_UP_SCORE: int = 30
    CAPTCHA_BLOCK_SCORE: int = 80

    ENV: str = "dev"

    @property
    def is_production(self) -> bool:
        return self.ENV.strip().lower() in ("prod", "production", "staging")

    @model_validator(mode="after")
    def _fail_fast_in_production(self) -> "Settings":
        """B5: 프로덕션에서 개발용 기본 시크릿/설정으로 부팅하면 즉시 실패시킨다."""
        if not self.is_production:
            return self
        problems: list[str] = []
        if not self.JWT_SECRET_KEY or self.JWT_SECRET_KEY == _INSECURE_JWT_DEFAULT:
            problems.append("JWT_SECRET_KEY가 설정되지 않았거나 개발용 기본값입니다.")
        if len(self.JWT_SECRET_KEY) < 32:
            problems.append("JWT_SECRET_KEY는 최소 32자 이상이어야 합니다.")
        if "*" in self.cors_origin_list:
            problems.append("프로덕션에서 CORS_ORIGINS 와일드카드(*)는 허용되지 않습니다.")
        if not self.smtp_enabled:
            # B8: 프로덕션에서 SMTP 미설정이면 메일이 '발송된 척' dry-run 되므로 부팅 거부.
            problems.append(
                "프로덕션에서 SMTP(SMTP_USER/SMTP_APP_PASSWORD)가 설정되지 않았습니다 "
                "— 인증/재설정 메일이 실제로 발송되지 않습니다."
            )
        if problems:
            raise ValueError(
                "프로덕션 설정 오류 (ENV=%s):\n - %s" % (self.ENV, "\n - ".join(problems))
            )
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def smtp_enabled(self) -> bool:
        return bool(self.SMTP_USER and self.SMTP_APP_PASSWORD)


@lru_cache
def get_settings() -> Settings:
    return Settings()
