from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.logging_config import setup_logging

setup_logging()  # 모든 모듈 로거 일관 초기화 (조용한 실패 방지)
settings = get_settings()

# 프로덕션에서는 API 문서(스키마·엔드포인트 전수) 비공개 — 공격 표면 축소
_docs_url = None if settings.is_production else "/docs"
_redoc_url = None if settings.is_production else "/redoc"
_openapi_url = None if settings.is_production else "/openapi.json"

app = FastAPI(
    title="CatChap API",
    version="0.1.0",
    description="어린이 교육용 CAPTCHA API 학습 서비스 — 1차: 인증/기관/학습 대시보드. "
    "메인 CAPTCHA 판별·교육 게임 API는 다음 단계(stub).",
    docs_url=_docs_url,
    redoc_url=_redoc_url,
    openapi_url=_openapi_url,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "catchap-backend"}
