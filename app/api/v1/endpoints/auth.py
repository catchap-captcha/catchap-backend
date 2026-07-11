from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.permissions import Principal, get_current_principal
from app.db.session import get_db
from app.schemas import auth as s
from app.services import auth_service, invite_service, onboarding_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


class ActivateStudentRequest(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    # 학생이 직접 정하는 로그인 아이디 (전역 유일). 컬럼이 String(50)이라 상한을 두어
    # 초과 입력이 DB DataError(500)로 새지 않고 422로 정직히 거부되게 한다.
    student_login_id: str = Field(min_length=3, max_length=50)
    nickname: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=200)


@router.post("/activate-student", response_model=s.TokenPair)
def activate_student(
    req: ActivateStudentRequest, request: Request, db: Session = Depends(get_db)
):
    """학교 발급 가입 코드로 학생 계정 활성화 → 즉시 로그인(토큰 발급).

    이메일·인증코드 없음. 아이는 코드 입력 후 별명·비밀번호만 정하면 가입 완료.
    코드 대입(brute-force) 방지: IP 기준 과도한 실패 시 차단(H1).
    """
    ip = request.client.host if request.client else "unknown"
    ident = f"activate:{ip}"
    auth_service._check_locked(db, ident)
    try:
        profile, _ = onboarding_service.activate_student(
            db, req.code, req.student_login_id, req.nickname, req.password
        )
    except HTTPException as e:
        if e.status_code in (404, 409, 410):  # 코드 오류·만료·재사용 = 실패로 집계
            auth_service._record_fail(db, ident)
        raise
    auth_service._reset_fails(db, ident)
    return auth_service.issue_tokens(db, profile.id, "student", "student")


@router.post("/login", response_model=s.TokenPair)
def login(req: s.LoginRequest, db: Session = Depends(get_db)):
    return auth_service.login(db, req)


@router.post("/ops-login", response_model=s.TokenPair)
def ops_login(req: s.LoginRequest, db: Session = Depends(get_db)):
    """운영자 전용 로그인 — 일반 로그인 폼과 분리된 숨겨진 경로(/ops/login)에서만 사용."""
    return auth_service.ops_login(db, req)


@router.post("/student-login", response_model=s.TokenPair)
def student_login(req: s.StudentLoginRequest, db: Session = Depends(get_db)):
    return auth_service.student_login(db, req)


@router.post("/refresh", response_model=s.TokenPair)
def refresh(req: s.RefreshRequest, db: Session = Depends(get_db)):
    return auth_service.refresh_tokens(db, req.refresh_token)


@router.post("/logout")
def logout(
    principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)
):
    auth_service.logout(db, principal.id)
    return {"ok": True}


@router.get("/me", response_model=s.MeResponse)
def me(principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)):
    return auth_service.get_me(db, principal)


@router.post("/email/send")
def send_email_code(req: s.EmailSendRequest, request: Request, db: Session = Depends(get_db)):
    # IP 기준 발송 상한(스팸/열거 완화) — 이메일 기준 상한은 서비스에서 별도 적용
    auth_service.rate_limit(db, f"emailsendip:{_client_ip(request)}", limit=40)
    auth_service.send_email_code(db, req.email, req.purpose, req.for_account)
    return {"ok": True}


@router.post("/check-student-id")
def check_student_id(req: s.CheckStudentIdRequest, db: Session = Depends(get_db)):
    """학생 아이디 전역 중복 확인 — 중복이면 사용 가능한 추천 아이디를 함께 반환."""
    available = auth_service.student_id_available(db, req.student_login_id)
    suggestions = (
        [] if available else auth_service.suggest_student_ids(db, req.student_login_id)
    )
    return {"available": available, "suggestions": suggestions}


@router.post("/email/verify")
def verify_email_code(req: s.EmailVerifyRequest, db: Session = Depends(get_db)):
    auth_service.verify_email_code(db, req.email, req.code, req.purpose)
    return {"verified": True}


@router.post("/register/parent")
def register_parent(req: s.RegisterParentRequest, db: Session = Depends(get_db)):
    user = auth_service.register_parent(db, req)
    return {"ok": True, "user_id": user.id}


@router.post("/register/teacher")
def register_teacher(req: s.RegisterTeacherRequest, db: Session = Depends(get_db)):
    user = auth_service.register_teacher(db, req)
    return {"ok": True, "user_id": user.id}


@router.post("/register/student")
def register_student(req: s.RegisterStudentRequest, db: Session = Depends(get_db)):
    student = auth_service.register_student(db, req)
    return {"ok": True, "student_id": student.id, "student_code": student.student_code}


@router.post("/register/org")
def register_org(req: s.RegisterOrgRequest, db: Session = Depends(get_db)):
    org = auth_service.register_org(db, req)
    return {"ok": True, "organization_id": org.id, "org_code": org.code}


@router.post("/password-reset/request")
def password_reset_request(
    req: s.PasswordResetRequest, request: Request, db: Session = Depends(get_db)
):
    auth_service.rate_limit(db, f"pwresetip:{_client_ip(request)}", limit=40)
    auth_service.password_reset_request(db, req.email)
    return {"ok": True}


@router.post("/password-reset/confirm")
def password_reset_confirm(req: s.PasswordResetConfirm, db: Session = Depends(get_db)):
    auth_service.password_reset_confirm(db, req)
    return {"ok": True}


@router.post("/verify-join-code")
def verify_join_code(
    req: s.JoinCodeVerifyRequest, request: Request, db: Session = Depends(get_db)
):
    """학생 가입 코드를 소비하지 않고 상태만 확인 — 아이디/비번 입력 전에 먼저 막기.
    저엔트로피 코드 열거 완화를 위해 IP 기준 시도 상한."""
    auth_service.rate_limit(db, f"verifyjoinip:{_client_ip(request)}", limit=40)
    return onboarding_service.check_join_code(db, req.code)


@router.post("/verify-org-code")
def verify_org_code(
    req: s.OrgCodeVerifyRequest, request: Request, db: Session = Depends(get_db)
):
    # 저엔트로피 기관코드 열거 완화 — IP 기준 시도 상한
    auth_service.rate_limit(db, f"verifyorgip:{_client_ip(request)}", limit=40)
    org = auth_service.verify_org_code(db, req.organization_id, req.code)
    if org:
        return {"valid": True, "organization_name": org.name}
    return {"valid": False}


@router.post("/verify-teacher-code")
def verify_teacher_code(
    req: s.OrgCodeVerifyRequest, request: Request, db: Session = Depends(get_db)
):
    # 교사 개별코드 열거 완화 — IP 기준 시도 상한
    auth_service.rate_limit(db, f"verifyteacherip:{_client_ip(request)}", limit=40)
    return {"valid": auth_service.verify_teacher_code(db, req.organization_id, req.code)}


@router.get("/invite/{token}")
def get_invite(token: str, request: Request, db: Session = Depends(get_db)):
    """교사 초대링크 검증 → 가입화면 프리필용 기관·교사코드 반환. 토큰이 곧 인증(무인증)."""
    auth_service.rate_limit(db, f"inviteip:{_client_ip(request)}", limit=60)
    return invite_service.get_invite(db, token)
