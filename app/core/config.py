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
