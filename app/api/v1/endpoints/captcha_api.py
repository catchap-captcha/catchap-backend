"""공개 캡차 API — 외부 사이트가 site_key로 호출 (메인 캡차 + 교육형).

  POST /captcha/v1/challenge   site_key(헤더) → 챌린지 발급 (요금제 게이팅·사용량 기록)
  POST /captcha/v1/verify      site_key + challenge_token + answer → 서버 채점 → verdict 토큰
  POST /captcha/v1/validate    secret_key + verdict_token → 최종 통과 검증 (고객 서버용, 1회용)

교육형도 같은 경로에 product='edu' 키를 쓰면 동작 (키에 과목이 박혀 있음).
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.security import sha256_hash
from app.db.session import get_db
from app.models import ApiKey
from app.services import auth_service
from app.services import captcha_service as cs

router = APIRouter(prefix="/captcha/v1", tags=["captcha-api"])

# 공개 엔드포인트 IP 레이트리밋 (분당) — 월 quota와 별개로 버스트/스크래핑 억제.
# 학교 NAT 뒤 다수 학생을 감안해 넉넉히, 봇 폭주는 막는 수준.
RATE_CHALLENGE_PER_MIN = 120
RATE_VERIFY_PER_MIN = 120
RATE_VALIDATE_PER_MIN = 240


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _key(db: Session, x_site_key: str | None) -> ApiKey:
    if not x_site_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="X-Site-Key 헤더가 필요합니다.")
    return cs.auth_site_key(db, x_site_key)


def _throttle(db: Session, request: Request, kind: str, limit: int) -> None:
    """IP 레이트리밋 — site_key 인증보다 먼저 실행해 무효 키 연타(DB 조회 DoS)도 막는다."""
    auth_service.rate_limit(db, f"cap{kind}:{_client_ip(request)}", limit=limit, window_seconds=60)


def _origin_guard(db: Session, request: Request, api: ApiKey) -> None:
    cs.assert_origin_allowed(
        db, api, request.headers.get("origin"), request.headers.get("referer")
    )


@router.post("/challenge")
def challenge(
    request: Request,
    x_site_key: str | None = Header(default=None),
    subject: str | None = None,  # edu 전용 과목 오버라이드 (?subject=수학) — 1st-party 인앱 임베드용
    db: Session = Depends(get_db),
):
    _throttle(db, request, "chall", RATE_CHALLENGE_PER_MIN)
    api = _key(db, x_site_key)
    _origin_guard(db, request, api)
    cs.assert_entitled(db, api)  # 요금제·quota 검사
    # 교육형 키는 발급 시 과목이 박혀 있지만, 우리 앱(과목별 게임화면)이 붙을 땐
    # 화면 과목에 맞춰 요청별로 과목을 바꿀 수 있게 허용한다. (EDU_SUBJECTS 안에서만)
    eff_subject = api.subject
    if api.product == "edu" and subject and subject in cs.EDU_SUBJECTS:
        eff_subject = subject
    ch = cs.make_challenge(api.product, eff_subject)
    cs.log_call(db, api, "captcha/challenge", 200)
    db.commit()
    return {"product": api.product, "subject": eff_subject, **ch}


class _VerifyReq(BaseModel):
    challenge_token: str
    answer: object  # 문자열 또는 배열(그림 다중선택)
    behavior: dict | None = None  # 교육형: 반응시간·재시도·조작 등 행동데이터


@router.post("/verify")
def verify(
    req: _VerifyReq,
    request: Request,
    x_site_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    _throttle(db, request, "verify", RATE_VERIFY_PER_MIN)
    api = _key(db, x_site_key)
    _origin_guard(db, request, api)
    result = cs.verify_challenge(db, req.challenge_token, req.answer)
    # 교육형 API는 통과/실패보다 '행동데이터 수집'이 목적 — 정답 여부와 무관하게 적재
    if api.product == "edu":
        behavior = req.behavior
        # 끌어다 놓기의 드롭 거리는 서버 채점값을 기록 (클라이언트 자기신고 대체)
        if "drop_distance_norm" in result:
            behavior = {**(behavior or {}), "drop_distance_norm": result["drop_distance_norm"]}
        cs.record_behavior(db, api, behavior, bool(result.get("success")))
    cs.log_call(db, api, "captcha/verify", 200 if result["success"] else 400)
    db.commit()
    return result


class _ValidateReq(BaseModel):
    verdict_token: str


@router.post("/validate")
def validate(
    req: _ValidateReq,
    request: Request,
    x_secret_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """고객 서버가 secret_key로 최종 검증 — 브라우저에서 받은 verdict가 진짜 통과인지.

    서버-대-서버 호출이라 Origin 검증은 없음(secret 자체가 인증). IP 레이트리밋만 건다.
    """
    if not x_secret_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="X-Secret-Key 헤더가 필요합니다.")
    auth_service.rate_limit(
        db, f"capvalidate:{_client_ip(request)}", limit=RATE_VALIDATE_PER_MIN, window_seconds=60,
    )
    api = (
        db.query(ApiKey)
        .filter(ApiKey.secret_key_hash == sha256_hash(x_secret_key), ApiKey.status == "active")
        .first()
    )
    if api is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 secret_key 입니다.")
    ok = cs.validate_verdict(db, req.verdict_token)
    cs.log_call(db, api, "captcha/validate", 200 if ok else 400)
    db.commit()
    return {"success": ok}
