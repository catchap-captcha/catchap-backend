"""교사 초대링크 — 기관 관리자/학년부장이 이메일로 초대 → 링크 클릭 시 기관·교사코드 프리필.

설계: 초대 시 교사 개별코드(T-xxxx)를 선발급(Membership user_id=NULL)하고 Invitation에 담아둔다.
accept 시 그 코드를 반환해 가입화면에 프리필하고, 실제 가입은 기존 register_teacher가 코드를 소비한다.
(register_teacher를 건드리지 않는다.)
"""

import secrets
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import generate_token, hash_password, sha256_hash
from app.email.smtp import render_template, send_email
from app.models import Institution, Invitation, Membership, Organization, User

settings = get_settings()

# 혼동 문자(0/O,1/I/L) 제외 — auth_service._generate_student_code와 동일 알파벳
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
INVITE_TTL_DAYS = 14
_ROLE_LABEL = {"teacher": "교사", "grade_head": "학년부장", "org_admin": "기관 관리자"}


def _generate_teacher_code(db: Session) -> str:
    for _ in range(50):
        code = "T-" + "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
        if not db.query(Membership).filter(Membership.teacher_code == code).first():
            return code
    raise HTTPException(
        status.HTTP_500_INTERNAL_SERVER_ERROR, detail="교사 코드 생성에 실패했어요. 다시 시도해 주세요."
    )


def create_teacher_invite(
    db: Session,
    *,
    organization_id: str,
    inviter_id: str,
    email: str,
    name: str | None = None,
    role: str = "teacher",
    class_name: str | None = None,
) -> str:
    """교사코드 선발급 + 초대 레코드 생성 + 초대 메일 발송. 원문 토큰을 반환(호출부가 필요 시 사용)."""
    email = email.strip().lower()
    if role not in ("teacher", "grade_head"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="초대할 수 없는 역할입니다.")
    org = db.get(Organization, organization_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="기관을 찾을 수 없습니다.")

    code = _generate_teacher_code(db)
    membership = Membership(
        user_id=None,
        organization_id=organization_id,
        role=role,
        status="pending",
        teacher_code=code,
        position=None,  # 직책(담임/교과)은 가입 후 관리자가 배정 — 초대엔 비워 둔다
        # 미리 담당 학급을 지정하면 예약 → 가입(코드 클레임) 시 그 반의 담임으로 자동 배정
        pending_class=(class_name.strip() if class_name and class_name.strip() else None),
        invited_by=inviter_id,
    )
    db.add(membership)
    db.flush()
    # 표시용 이름/이메일은 pending User 자리로 보관(가입 시 클레임). add_teacher와 동일 패턴이라
    # 교사 목록에서 초대 대기 교사도 이름이 보이고, position에 이름이 섞이지 않는다.
    if name and name.strip():
        existing_user = db.query(User).filter(User.email == email).first()
        if existing_user is None:
            placeholder = User(
                email=email,
                password_hash=hash_password(generate_token()[:32]),
                name=name.strip(),
                role="teacher",
                status="pending",
                organization_id=organization_id,
            )
            db.add(placeholder)
            db.flush()
            membership.user_id = placeholder.id
    raw = generate_token()
    db.add(
        Invitation(
            organization_id=organization_id,
            email=email,
            role=role,
            token_hash=sha256_hash(raw),
            teacher_code=code,
            invited_by=inviter_id,
            expires_at=datetime.now() + timedelta(days=INVITE_TTL_DAYS),
            status="pending",
        )
    )
    db.flush()

    accept_url = f"{settings.FRONTEND_URL.rstrip('/')}/invite?token={raw}"
    html = render_template(
        "invitation.html",
        org_name=org.name,
        role_name=_ROLE_LABEL.get(role, "교사"),
        accept_url=accept_url,
    )
    send_email(db, to_email=email, subject=f"[CatChap] {org.name} {_ROLE_LABEL.get(role, '교사')} 초대", html=html)
    return raw


def check_invite_token(
    db: Session,
    raw_token: str | None,
    *,
    email: str,
    organization_id: str,
    teacher_code: str,
) -> bool:
    """가입 시 넘어온 초대 토큰이 (이메일·기관·교사코드)와 일치하는 유효한 초대인지 확인.
    True면 초대 메일 수신으로 이메일 소유가 증명된 것이라 별도 이메일 인증코드를 생략할 수 있다."""
    if not raw_token:
        return False
    inv = (
        db.query(Invitation)
        .filter(Invitation.token_hash == sha256_hash(raw_token))
        .first()
    )
    if inv is None or inv.status != "pending" or inv.expires_at < datetime.now():
        return False
    return (
        inv.email == email.strip().lower()
        and inv.organization_id == organization_id
        and (inv.teacher_code or "") == (teacher_code or "").strip().upper()
    )


def get_invite(db: Session, raw_token: str) -> dict:
    """초대 토큰 검증 → 가입 프리필용 정보 반환. 만료/사용됨이면 4xx."""
    inv = db.query(Invitation).filter(Invitation.token_hash == sha256_hash(raw_token)).first()
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="유효하지 않은 초대 링크예요.")
    if inv.status != "pending":
        raise HTTPException(status.HTTP_410_GONE, detail="이미 사용되었거나 취소된 초대예요.")
    if inv.expires_at < datetime.now():
        raise HTTPException(status.HTTP_410_GONE, detail="초대 링크가 만료됐어요. 다시 요청해 주세요.")
    # 초대 대기 교사는 이름 표시용 placeholder User(status=pending)를 미리 둔다.
    # 그 계정이 실제 가입으로 활성화(active)됐으면 초대는 사용 완료로 간주한다.
    m = (
        db.query(Membership)
        .filter(Membership.teacher_code == inv.teacher_code)
        .first()
        if inv.teacher_code
        else None
    )
    placeholder = db.get(User, m.user_id) if (m and m.user_id) else None
    if placeholder is not None and placeholder.status != "pending":
        raise HTTPException(status.HTTP_410_GONE, detail="이미 가입에 사용된 초대예요.")

    org = db.get(Organization, inv.organization_id)
    # 기관에 연결된 디렉터리(Institution)가 있으면 위치·유형도 함께 반환(가입화면 표시용)
    inst = (
        db.query(Institution)
        .filter(Institution.organization_id == inv.organization_id)
        .first()
    )
    return {
        "valid": True,
        "organization_id": inv.organization_id,
        "organization_name": org.name if org else "",
        "email": inv.email,
        "role": inv.role,
        "teacher_code": inv.teacher_code,
        # 초대 시 관리자가 입력한 교사 이름(선택) — 가입화면 이름칸 자동 입력용.
        "name": (placeholder.name if placeholder else None),
        "inst_type": inst.inst_type if inst else "",
        "sido": inst.sido if inst else "",
        "sigungu": inst.sigungu if inst else "",
        "dong": inst.dong if inst else "",
        "road_address": inst.road_address if inst else "",
    }
