import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy.exc import IntegrityError

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

_log = logging.getLogger("catchap.main")

# JSON 본문 상한 — 행동 궤적(trace) 같은 배열 입력이 무제한으로 커져 메모리를 소모하지
# 않게 한다. 파일 업로드 엔드포인트가 없어 전역 1MB로 충분 (궤적 2000점 ≈ 40KB).
MAX_BODY_BYTES = 1_000_000


@app.middleware("http")
async def _limit_body_size(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_BODY_BYTES:
        return JSONResponse(status_code=413, content={"detail": "요청 본문이 너무 큽니다."})
    return await call_next(request)


# 공개 캡차 API(/captcha/v1/*)는 고객사 도메인(임의 출처)의 브라우저가 호출한다 —
# 전역 CORSMiddleware는 자사 프론트 오리진만 허용하므로 이 경로만 와일드카드로 연다.
# 쿠키 인증이 아니라 X-Site-Key 헤더 인증이라 ACAO:* 가 안전하고, 실제 도메인 제한은
# 서버측 Origin 검증(captcha_service.assert_origin_allowed)이 수행한다.
# (뒤에 추가된 미들웨어가 최외곽 → 전역 CORS가 외부 오리진 preflight를 400으로
#  거절하기 전에 여기서 가로챈다)
_CAPTCHA_PUBLIC_PREFIX = "/api/v1/captcha/v1"


@app.middleware("http")
async def _captcha_public_cors(request: Request, call_next):
    if not request.url.path.startswith(_CAPTCHA_PUBLIC_PREFIX):
        return await call_next(request)
    if request.method == "OPTIONS":
        return Response(
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                # Authorization: 인앱(1st-party) 위젯이 학생 토큰을 실어 적립 — ACAO:* 에서도
                # 쿠키가 아닌 명시 헤더라 안전하고, 토큰 검증은 서버(_optional_student)가 한다.
                "Access-Control-Allow-Headers": "Content-Type, X-Site-Key, X-Secret-Key, Authorization",
                "Access-Control-Max-Age": "600",
            },
        )
    response = await call_next(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


@app.exception_handler(IntegrityError)
def _integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    """DB 무결성 위반(UNIQUE 충돌 등)을 500 대신 409로 — 사용자에게 명확한 메시지.

    find-or-create/보상 경로는 각자 IntegrityError를 캐치해 재조회/스킵하지만,
    미처리로 전파된 경우의 안전망이다.
    """
    _log.warning("IntegrityError → 409: %s", exc)
    return JSONResponse(
        status_code=409,
        content={"detail": "이미 존재하거나 중복된 데이터예요. 잠시 후 다시 시도해 주세요."},
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "catchap-backend"}
