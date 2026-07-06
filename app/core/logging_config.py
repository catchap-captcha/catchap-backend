"""중앙 로깅 설정.

앱 시작 시 한 번 호출(main.py)해서 모든 모듈의 logging.getLogger(...)가
일관된 포맷·레벨로 출력되도록 한다. 개발은 DEBUG, 운영은 INFO 권장.
"""

import logging
from logging.config import dictConfig

from app.core.config import get_settings


def setup_logging() -> None:
    settings = get_settings()
    level = "DEBUG" if settings.ENV == "dev" else "INFO"
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "level": level,
                }
            },
            "loggers": {
                # 앱 전용 로거는 catchap.* 네임스페이스로 통일
                "catchap": {"handlers": ["console"], "level": level, "propagate": False},
                # 서드파티 소음 억제
                "uvicorn.access": {"level": "WARNING"},
                "sqlalchemy.engine": {"level": "WARNING"},
            },
            "root": {"handlers": ["console"], "level": "WARNING"},
        }
    )


def get_logger(name: str) -> logging.Logger:
    """앱 로거 헬퍼 — catchap.* 네임스페이스로 통일해서 사용한다."""
    return logging.getLogger(f"catchap.{name}")
