from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.permissions import Principal, get_current_principal
from app.db.session import get_db
from app.schemas import auth as s
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


class ActivateStudentRequest(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    student_login_id: str = Field(min_length=3, max_length=50)
    nickname: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=200)


@router.post("/activate-student")
def activate_student(req: ActivateStudentRequest, db: Session = Depends(get_db)):
    """학교 발급 가입 코드 활성화 — 제품 전환(학교 은퇴, 2026-07-18)으로 접수 종료.

    이 통로는 생년월일·연령을 수집하지 않아(저학년용 설계) 연령 게이트(만 14세 미만
    보호자 동의)를 우회한다 — 학교 은퇴로 코드 공급도 끊겼으므로 미사용 코드로도
    가입되지 않게 여기서 봉쇄한다. 이메일 가입(/register/student)이 유일한 통로."""
    raise HTTPException(
        status.HTTP_410_GONE,
        detail="학교 코드 가입이 종료되었어요. 이메일 가입으로 시작해 주세요.",
    )


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


# (verify-join-code · verify-org-code · verify-teacher-code · invite/{token} 는
#  학교/교사 은퇴(0717~18)로 제거 — 소비 화면이 없다. 종전 코드는 git 이력 참고.)
