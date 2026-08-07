"""소셜 로그인 API — 카카오·네이버·구글 (학생 계정 전용).

  GET    /auth/social/providers            설정된 provider 목록(프론트 버튼 노출 판단)
  GET    /auth/social/{provider}/authorize provider 동의 화면 URL + state 발급
  POST   /auth/social/{provider}/callback  code·state → 로그인 또는 signup_required
  POST   /auth/social/signup               생년월일 확인 후 계정 생성 + 토큰 발급
  GET    /auth/social/connections          내 연결 목록 (로그인 필요)
  POST   /auth/social/{provider}/connect   로그인 상태에서 소셜 계정 추가 연결
  DELETE /auth/social/{provider}           연결 해제 (마지막 로그인 수단이면 거절)

콘솔 계정(운영자·강사)은 **자동 연결 대상이 아니다** — 이메일이 같아도 붙이지 않는다.
다만 본인이 비밀번호로 로그인한 뒤 /connect 로 명시적으로 연결하면 그때부터 소셜 로그인을
쓸 수 있다. 고권한 계정을 외부 IdP에 여는 결정을 '본인의 명시적 행위'로만 만드는 설계다.

판정 로직은 services/social_login_service.py, provider HTTP 규격은 services/social_auth.py.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.permissions import Principal, get_current_principal
from app.db.session import get_db
from app.schemas import auth as s
from app.services import auth_service, social_login_service

router = APIRouter(prefix="/auth/social", tags=["auth"])

# 콜백 남용 상한(IP 기준) — 정상 사용은 로그인 한 번에 1회다. 인가 코드 대입·스팸을 억제한다.
RATE_CALLBACK_PER_HOUR = 60


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _subject_label(principal: Principal) -> str:
    return "학생" if principal.kind == "student" else "콘솔 계정"


@router.get("/providers")
def providers():
    """설정된 소셜 로그인 목록 — enabled=false면 프론트가 버튼을 감춘다."""
    return {"providers": social_login_service.available_providers()}


@router.get("/connections")
def my_connections(
    principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)
):
    """내 소셜 연결 목록 — 학생·콘솔 계정 공용."""
    return social_login_service.connections(db, principal)


@router.post("/signup", response_model=s.SocialLoginResponse)
def social_signup(req: s.SocialSignupRequest, db: Session = Depends(get_db)):
    """소셜 신규 가입 마무리 — 만 14세 미만은 여기서 400으로 막힌다(연령 게이트)."""
    return social_login_service.signup(db, req)


@router.get("/{provider}/authorize", response_model=s.SocialAuthorizeResponse)
def authorize(
    provider: str,
    redirect_uri: str | None = Query(default=None, max_length=500),
):
    """provider 동의 화면 URL 발급. 프론트는 이 URL로 이동시키기만 하면 된다.

    redirect_uri는 서버 허용목록 안의 값만 받는다(오픈 리다이렉트 차단). 미지정 시 기본값."""
    return social_login_service.authorize(provider, redirect_uri)


@router.post("/{provider}/callback", response_model=s.SocialLoginResponse)
def callback(
    provider: str,
    req: s.SocialCallbackRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """인가 코드 → 로그인. 신규 사용자는 아직 계정을 만들지 않고 signup_required를 준다."""
    auth_service.rate_limit(
        db, f"socialcb:{_client_ip(request)}", limit=RATE_CALLBACK_PER_HOUR
    )
    return social_login_service.callback(db, provider, code=req.code, state=req.state)


@router.post("/{provider}/connect")
def connect(
    provider: str,
    req: s.SocialCallbackRequest,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """로그인한 계정에 소셜 계정을 추가 연결한다(계정 설정 화면).

    ★콘솔 계정이 소셜 로그인을 쓸 수 있게 되는 유일한 통로다 — 자동 연결은 없다."""
    return social_login_service.connect(
        db, principal, provider, code=req.code, state=req.state
    )


@router.delete("/{provider}")
def disconnect(
    provider: str,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    if provider not in [p["provider"] for p in social_login_service.available_providers()]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="지원하지 않는 소셜 로그인이에요.")
    return social_login_service.disconnect(db, principal, provider)
