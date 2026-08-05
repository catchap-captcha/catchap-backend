import asyncio
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.logging_config import setup_logging

setup_logging()  # 모든 모듈 로거 일관 초기화 (조용한 실패 방지)
settings = get_settings()


def _collect_cluster_metrics_once(interval_sec: int) -> None:
    """클러스터 지표 1회 수집 — ★동기 코드라 이벤트 루프 밖(스레드)에서 부른다.

    ⚠️백엔드 파드가 2벌이라 둘 다 이 고리를 돈다. 그대로 두면 표본이 2배로 쌓이므로
    "직전 수집이 충분히 오래됐을 때만" 걷는다. 리더 선출 같은 무거운 장치 대신 ★DB 한 줄
    조회로 겹침을 줄인다 — 완벽한 배타는 아니지만, 겹쳐도 시간별 롤업 평균은 sum/count라
    옳은 값이 나오고 raw 표본이 조금 촘촘해질 뿐이다.

    ★★신선도를 볼 때 ★노드 행만 본다. server_metrics 전체를 보면, 클러스터 밖에서
    에이전트가 밀어넣는 서버(GPU STT 워커)의 시각이 항상 신선해서 ★클러스터 수집이
    영원히 건너뛰어진다. 노드 행은 이 수집기만 쓰므로 자기 주기를 정확히 잰다."""
    from app.api.v1.endpoints.monitoring import collect_cluster
    from app.db.session import SessionLocal
    from app.models import ServerMetric
    from app.services.cluster_metrics import NODE_KEY_PREFIX

    db = SessionLocal()
    try:
        newest = (
            db.query(func.max(ServerMetric.collected_at))
            .filter(ServerMetric.server_key.startswith(NODE_KEY_PREFIX))
            .scalar()
        )
        if newest and (datetime.now() - newest).total_seconds() < interval_sec * 0.8:
            return  # 다른 파드(또는 방금 열린 대시보드)가 이미 걷었다
        collect_cluster(db)
    except Exception:
        db.rollback()  # 쓰다 만 트랜잭션을 남기지 않는다 — 다음 주기가 깨끗하게 시작한다
        raise
    finally:
        db.close()


async def _cluster_metrics_loop(interval_sec: int) -> None:
    """주기 수집 고리 — ★화면을 아무도 안 열어도 추이가 이어지게.

    옛 구조는 대시보드를 열 때만 백엔드 자신을 쟀다. 그래서 아무도 안 보면 그래프에 구멍이
    났다(0805 실측: backend 행이 ★8일 전 값이었다). 배경에서 걷어야 추이가 성립한다."""
    while True:
        await asyncio.sleep(interval_sec)
        try:
            await asyncio.to_thread(_collect_cluster_metrics_once, interval_sec)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — 한 번 실패해도 고리는 계속 돈다
            _log.warning("클러스터 지표 배경 수집 실패: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """기동 시 1회: 프로세스 재시작으로 고아가 된 AI 문항 생성 잡을 정리한다.
    그리고 클러스터 지표 배경 수집 고리를 띄운다(쿠버네티스 배포에서만).

    생성 잡은 프로세스 내 BackgroundTasks로 돌아, 재배포·크래시 순간 'running'이던 잡은
    마감 코드가 다시 안 돌아 DB에 유령 행으로 남는다(프론트는 "생성 중…" 무한 표시).
    여기서 오래 멈춘 잡만 error로 정직하게 마감한다. DB가 아직 준비 안 됐어도(마이그레이션
    전 등) 기동은 막지 않는다 — 정리는 다음 기동에 다시 시도된다."""
    from app.db.session import SessionLocal
    from app.services.lecture_service import purge_expired_trash, sweep_stuck_gen_jobs

    try:
        db = SessionLocal()
        try:
            swept = sweep_stuck_gen_jobs(db)
            if swept:
                _log.warning("기동 정리: 고아 문항생성 잡 %d개를 error로 마감", swept)
            # 휴지통 30일 만료 강의 자동 완전삭제 — 스케줄러가 없어 기동(배포)마다 정리한다.
            # 콘솔의 휴지통 조회 시에도 기회적으로 돌아, 둘이 겹쳐 30일 정책을 실현한다.
            purged = purge_expired_trash(db)
            if purged:
                _log.warning("기동 정리: 30일 지난 휴지통 강의 %d개 완전삭제", purged)
        finally:
            db.close()
    except SQLAlchemyError as exc:
        _log.warning("기동 정리 건너뜀(DB 미준비 등): %s", exc)

    # PROMETHEUS_URL이 비면 띄우지 않는다 — 로컬·VM 배포는 종전대로 에이전트 push만 쓴다.
    task: asyncio.Task | None = None
    if settings.PROMETHEUS_URL.strip():
        interval = max(10, settings.CLUSTER_METRICS_INTERVAL_SEC)  # 너무 짧으면 표본이 폭증한다
        task = asyncio.create_task(_cluster_metrics_loop(interval))
        _log.info("클러스터 지표 배경 수집 시작 — %d초 주기", interval)
    try:
        yield
    finally:
        if task is not None:
            task.cancel()  # 종료 신호를 받으면 다음 sleep에서 깨어나 끝난다
            try:
                await task
            except asyncio.CancelledError:
                pass


def _init_sentry() -> None:
    """에러 트래킹 — SENTRY_DSN이 있으면 활성, 없으면 no-op(미설정 시 조용히 비활성).

    아동 개인정보·비밀이 에러 페이로드로 새지 않게 send_default_pii=False(요청 본문·쿠키·
    유저 기본 미첨부) + before_send에서 알려진 PII 키를 마스킹한다."""
    dsn = (getattr(settings, "SENTRY_DSN", "") or "").strip()
    if not dsn:
        return
    import sentry_sdk

    _PII = {"real_name", "name", "age", "gender", "student_login_id", "login_id", "email",
            "password", "password_hash", "access_token", "refresh_token", "token", "authorization"}

    def _scrub(event, _hint):
        req = event.get("request")
        if isinstance(req, dict):
            req.pop("data", None)
            req.pop("cookies", None)
            headers = req.get("headers")
            if isinstance(headers, dict):
                for k in list(headers):
                    if k.lower() in _PII:
                        headers[k] = "[scrubbed]"
        return event

    sentry_sdk.init(
        dsn=dsn,
        environment="production" if settings.is_production else "dev",
        traces_sample_rate=0.0,   # 에러만 — 성능 트레이싱 off
        send_default_pii=False,   # 요청 본문/유저/쿠키 기본 미첨부(아동 PII 보호)
        before_send=_scrub,
    )


_init_sentry()

# 프로덕션에서는 API 문서(스키마·엔드포인트 전수) 비공개 — 공격 표면 축소
_docs_url = None if settings.is_production else "/docs"
_redoc_url = None if settings.is_production else "/redoc"
_openapi_url = None if settings.is_production else "/openapi.json"

app = FastAPI(
    title="CatChap API",
    version="0.1.0",
    description="어린이 교육용 CAPTCHA API 학습 서비스 — 인증/기관/학습 대시보드, "
    "숲속 마을 메인 CAPTCHA, 교육형 CAPTCHA API에 더해 강의 시청 검증"
    "(영상 시청 중 체크포인트 캡차 게이트로 실제 시청을 확인)을 제공합니다.",
    docs_url=_docs_url,
    redoc_url=_redoc_url,
    openapi_url=_openapi_url,
    lifespan=lifespan,
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

# 요청 본문 상한 — 행동 궤적(trace) 같은 배열 입력이 무제한으로 커져 메모리를 소모하지
# 않게 한다. 기본 1MB(궤적 2000점 ≈ 40KB). 유일한 예외는 강의 영상 업로드
# (POST /api/v1/ops/lectures, multipart)로, 그 경로+메서드만 MAX_UPLOAD_BYTES까지 허용한다.
# 이 미들웨어는 Content-Length 헤더만 검사하고 바디를 버퍼링하지 않으므로, 헤더 위조에
# 대비한 실제 누적 바이트 재검사는 업로드 엔드포인트(lectures.py)가 청크 복사 중에 수행한다.
MAX_BODY_BYTES = 1_000_000
_UPLOAD_PATH = "/api/v1/ops/lectures"  # 정확 일치(하위 경로 PUT/questions 등은 1MB 유지)
# 강의 자료(자료실) 파일 업로드 — POST /ops/lectures/{id}/materials 정확 형태만.
# 상한은 영상(5GB)과 분리한 MAX_MATERIAL_UPLOAD_BYTES(50MB): 자료는 문서류라 50MB면
# 충분하고, 영상 상한을 그대로 열면 자료 경로가 대용량 업로드 표면(디스크 소모)이 된다.
# multipart일 때만 예외 — 같은 경로의 link 생성(JSON 본문)은 1MB로 충분하며,
# JSON 파서는 본문 전체를 메모리에 올리므로 예외를 주면 안 된다.
_MATERIAL_UPLOAD_RE = re.compile(r"^/api/v1/ops/lectures/[^/]+/materials$")
# 확인 문항 이미지(강의 화면 캡처) 업로드 — POST /ops/lectures/{id}/questions/{qid}/images
# 정확 형태만. 상한은 자료(50MB)보다 훨씬 작은 MAX_QUESTION_IMAGE_BYTES(5MB): 캡처 PNG는
# 1~3MB 수준이라 충분하고, multipart일 때만 예외(같은 경로에 JSON을 보내는 시도는 1MB 유지).
_QUESTION_IMAGE_UPLOAD_RE = re.compile(
    r"^/api/v1/ops/lectures/[^/]+/questions/[^/]+/images$"
)


@app.middleware("http")
async def _limit_body_size(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl and cl.isdigit():
        content_type = (request.headers.get("content-type") or "").lower()
        if request.method == "POST" and request.url.path == _UPLOAD_PATH:
            limit = settings.MAX_UPLOAD_BYTES
        elif (
            request.method == "POST"
            and content_type.startswith("multipart/form-data")
            and _MATERIAL_UPLOAD_RE.match(request.url.path)
        ):
            limit = settings.MAX_MATERIAL_UPLOAD_BYTES
        elif (
            request.method == "POST"
            and content_type.startswith("multipart/form-data")
            and _QUESTION_IMAGE_UPLOAD_RE.match(request.url.path)
        ):
            limit = settings.MAX_QUESTION_IMAGE_BYTES
        else:
            limit = MAX_BODY_BYTES
        if int(cl) > limit:
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
