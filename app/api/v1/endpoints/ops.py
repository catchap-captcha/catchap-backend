"""운영자(ops) API — seed 기반 최소 응답 + 기관 가입 승인."""

import csv
import hashlib
import io
import secrets
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from html import escape

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.utils.helpers import audit

from app.core.config import get_settings
from app.core.permissions import Principal, require_ops
from app.core.security import hash_password, verify_password
from app.db.session import get_db
from app.email.smtp import send_email
from app.models import (
    ApiKey,
    ApiUsageLog,
    AuditLog,
    BehaviorSummary,
    BehaviorTrace,
    CaptchaSetting,
    ClassRoom,
    Inquiry,
    InquiryReply,
    Invitation,
    Invoice,
    Membership,
    ModelVersion,
    Organization,
    OrgRegistrationRequest,
    PaymentMethod,
    Plan,
    Site,
    StudentProfile,
    Subscription,
    User,
)
from app.services import captcha_service as _cs

router = APIRouter(prefix="/ops", tags=["ops"])


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@router.get("/dashboard")
def dashboard(principal: Principal = Depends(require_ops), db: Session = Depends(get_db)):
    return {
        "organizations": db.query(Organization).count(),
        "users": db.query(User).filter(User.status != "disabled").count(),
        "students": db.query(StudentProfile).filter(StudentProfile.status != "disabled").count(),
        "active_api_keys": db.query(ApiKey).filter(ApiKey.status == "active").count(),
        "open_inquiries": db.query(Inquiry).filter(Inquiry.status == "received").count(),
        "audit_logs": db.query(AuditLog).count(),
        # 실측 — ApiUsageLog는 로컬(KST) created_at이므로 자정 경계도 로컬로
        **_api_calls_today(db),
    }


def _api_calls_today(db: Session) -> dict:
    day_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    total = (
        db.query(func.count(ApiUsageLog.id))
        .filter(ApiUsageLog.created_at >= day_start)
        .scalar()
        or 0
    )
    errors = (
        db.query(func.count(ApiUsageLog.id))
        .filter(ApiUsageLog.created_at >= day_start, ApiUsageLog.status_code >= 500)
        .scalar()
        or 0
    )
    return {
        "api_calls_today": int(total),
        "error_rate": f"{(errors / total * 100):.1f}%" if total else "0%",
    }


@router.get("/orgs")
def orgs(
    search: str | None = None,  # 기관명/코드/담당 이메일 부분일치
    page: int | None = None,  # 없으면 기존 배열(하위호환 — 키 발급 모달 드롭다운 등), 있으면 페이지 응답
    page_size: int = 50,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    q = db.query(Organization)
    if search:
        like = f"%{search.strip()}%"
        q = q.filter(
            Organization.name.like(like)
            | Organization.code.like(like)
            | Organization.contact_email.like(like)
        )
    total = q.count()
    # 최신 기관 먼저 — 방금 만든 기관이 마지막 페이지로 숨지 않게(페이지네이션 전환 후 확인성)
    q = q.order_by(Organization.created_at.desc())
    if page is not None:
        page = max(1, page)
        page_size = max(1, min(200, page_size))
        q = q.offset((page - 1) * page_size).limit(page_size)
    rows = q.all()
    # 기관마다 학생 수 COUNT를 따로 날리지 않고 GROUP BY 한 번으로 집계
    counts = dict(
        db.query(StudentProfile.organization_id, func.count(StudentProfile.id))
        .filter(StudentProfile.status != "disabled")
        .group_by(StudentProfile.organization_id)
        .all()
    )
    items = [_org_admin_row(db, o, student_count=int(counts.get(o.id, 0))) for o in rows]
    if page is None:
        return items  # 하위호환 — 기존 소비처는 배열을 기대한다
    return {
        "items": items,
        "total": total,  # 검색 조건 반영된 건수 (페이지 계산용)
        "page": page,
        "page_size": page_size,
        # 헤더 요약용 전체 집계 — 검색과 무관한 전체 기준
        "total_all": db.query(func.count(Organization.id)).scalar() or 0,
        "total_students": int(sum(counts.values())),
    }


# ---------------------------------------------------------------- 기관 등록/수정/삭제 (운영자)
# 운영자 콘솔은 기관 '엔티티'만 관리한다. 학생 명단·실명 등 기관 내부 데이터는
# 여기서 다루지 않으며(아동 PII 분리), 그건 기관 관리자/학년부장의 /orgs/* 콘솔 몫이다.
def _org_admin_row(db: Session, o: Organization, student_count: int | None = None) -> dict:
    """운영자 기관 목록/상세 행 — 기관 메타 + 학생 수(집계값만, PII 아님).

    목록처럼 여러 행을 만들 때는 student_count를 미리 집계해 넘겨 기관마다 COUNT하지 않는다."""
    if student_count is None:
        student_count = (
            db.query(StudentProfile)
            .filter(StudentProfile.organization_id == o.id, StudentProfile.status != "disabled")
            .count()
        )
    return {
        "id": o.id,
        "name": o.name,
        "code": o.code,
        "org_type": o.org_type,
        "status": o.status,
        "contact_email": o.contact_email,
        "contact_phone": o.contact_phone,
        "address": o.address,
        "business_number": o.business_number,
        "edu_subjects": list(o.edu_subjects or []),  # 구매한 교육형 과목(발급 허용 범위)
        "students": student_count,
        "created_at": o.created_at.isoformat() if o.created_at else None,
    }


class _OrgCreateReq(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    org_type: str = Field(default="초등학교", max_length=30)
    status: str = Field(default="active", pattern="^(active|pending|disabled)$")
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, max_length=30)
    address: str | None = Field(default=None, max_length=255)
    business_number: str | None = Field(default=None, max_length=30)
    # 기관을 실제로 쓰려면 로그인 가능한 관리자(교장) 계정이 필요하다.
    admin_name: str = Field(min_length=1, max_length=100)
    admin_email: EmailStr


class _OrgUpdateReq(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    org_type: str | None = Field(default=None, max_length=30)
    status: str | None = Field(default=None, pattern="^(active|pending|disabled)$")
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, max_length=30)
    address: str | None = Field(default=None, max_length=255)
    business_number: str | None = Field(default=None, max_length=30)


@router.post("/orgs")
def ops_create_org(
    req: _OrgCreateReq,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """기관 신규 등록 — 기관 + 관리자(교장) 계정 생성. 임시 비밀번호는 응답에서만 1회 노출.

    자체 가입(register_org)과 달리 운영자가 직접 만드는 경로라 이메일 인증을 생략하고
    바로 사용 가능(active) 상태로 만든다.
    """
    admin_email = req.admin_email.strip().lower()
    if db.query(User).filter(User.email == admin_email).first():
        raise HTTPException(status.HTTP_409_CONFLICT, detail="이미 가입된 관리자 이메일입니다.")
    if req.business_number and (
        db.query(Organization)
        .filter(Organization.business_number == req.business_number)
        .first()
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="이미 등록된 사업자번호입니다.")

    from app.services.auth_service import _generate_org_code

    org = Organization(
        name=req.name,
        code=_generate_org_code(db, req.name),
        org_type=req.org_type,
        status=req.status,
        contact_email=(req.contact_email.strip().lower() if req.contact_email else admin_email),
        contact_phone=req.contact_phone,
        address=req.address,
        business_number=req.business_number,
        code_expires_at=_now() + timedelta(days=365),
    )
    db.add(org)
    db.flush()

    temp_password = secrets.token_urlsafe(9)
    admin = User(
        email=admin_email,
        password_hash=hash_password(temp_password),
        name=req.admin_name,
        phone=req.contact_phone,
        role="org_admin",
        status="active",
        organization_id=org.id,
        email_verified_at=datetime.now(),  # 사용자 기록 시각 — 로컬(KST) 규약
        must_change_password=True,  # 첫 로그인 시 새 비번 강제 (운영자·승인 발급과 통일)
    )
    db.add(admin)
    db.flush()
    db.add(
        Membership(
            user_id=admin.id,
            organization_id=org.id,
            role="org_admin",
            status="active",
            joined_at=datetime.now(),  # 사용자 기록 시각 — 로컬(KST) 규약
        )
    )
    # 기본 요금제(Basic) 연결 — 키 발급·요금제 게이팅이 동작하도록.
    # plans.key는 대문자('Basic') — 과거 소문자 조회 버그로 신규 기관에 구독이 안 붙던 것 수정.
    basic = db.query(Plan).filter(func.lower(Plan.key) == "basic").first()
    if basic:
        db.add(Subscription(organization_id=org.id, plan_id=basic.id))

    # 관리자 임시 비번을 본인 이메일로 자동 통보 (평문 미저장, 이 순간에만 발송)
    pw = escape(temp_password)
    html = (
        "<div style='font-family:sans-serif;line-height:1.7;color:#333'>"
        f"<p>{escape(req.admin_name)}님, 안녕하세요. CatChap 운영팀입니다.</p>"
        f"<p><b>{escape(org.name)}</b>의 기관 관리자 계정이 발급되었습니다.</p>"
        "<div style='margin:16px 0;padding:14px 16px;background:#fff3ee;border-radius:10px'>"
        f"<b>로그인 이메일</b><br>{escape(admin_email)}<br><br>"
        f"<b>임시 비밀번호</b><br>"
        f"<span style='font-size:18px;font-weight:700;color:#e85b2a;letter-spacing:1px'>{pw}</span>"
        "</div>"
        "<p>보안을 위해 <b>첫 로그인 후 반드시 새 비밀번호로 변경</b>해 주세요.</p>"
        "<p>감사합니다. 🐾</p></div>"
    )
    sent = send_email(
        db, to_email=admin_email, subject="[CatChap] 기관 관리자 계정이 발급되었습니다",
        html=html, user_id=admin.id,
    )
    email_status = "dry_run" if not get_settings().smtp_enabled else ("sent" if sent else "failed")
    db.add(
        AuditLog(
            actor_user_id=principal.id,
            organization_id=org.id,
            action="org.create",
            target_type="organization",
            target_id=org.id,
            after_json={
                "name": org.name, "code": org.code,
                "admin_email": admin_email, "email_status": email_status,
            },
        )
    )
    db.commit()
    return {
        "ok": True,
        **_org_admin_row(db, org),
        "admin_email": admin_email,
        "admin_temp_password": temp_password,  # 이메일 실패/dry-run 시 수동 전달용 (1회 노출)
        "admin_email_status": email_status,
    }


@router.patch("/orgs/{org_id}")
def ops_update_org(
    org_id: str,
    req: _OrgUpdateReq,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """기관 정보 수정 — 이름·유형·상태·연락처. 상태를 disabled로 두면 이용 중지."""
    org = db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="기관을 찾을 수 없습니다.")
    if req.business_number and req.business_number != org.business_number and (
        db.query(Organization)
        .filter(Organization.business_number == req.business_number, Organization.id != org_id)
        .first()
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="이미 등록된 사업자번호입니다.")
    fields = ("name", "org_type", "status", "contact_email", "contact_phone", "address", "business_number")
    before = {f: getattr(org, f) for f in fields}
    for f in fields:
        value = getattr(req, f)
        if value is not None:
            setattr(org, f, value.strip().lower() if f == "contact_email" else value)
    db.add(
        AuditLog(
            actor_user_id=principal.id,
            organization_id=org.id,
            action="org.update",
            target_type="organization",
            target_id=org.id,
            before_json=before,
            after_json={f: getattr(org, f) for f in fields},
        )
    )
    db.commit()
    return _org_admin_row(db, org)


@router.delete("/orgs/{org_id}")
def ops_delete_org(
    org_id: str,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """기관 삭제 — 소속 데이터(학생·API 키)가 없는 빈 기관만 실제 삭제한다.

    학생/키가 남아 있으면 삭제 대신 409를 반환하고, 이용을 막으려면 '중지'(status=disabled)를
    쓰도록 안내한다. 아동 학습 데이터를 실수로 고아(orphan)로 만들지 않기 위한 안전장치.
    """
    org = db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="기관을 찾을 수 없습니다.")
    student_count = db.query(StudentProfile).filter(StudentProfile.organization_id == org_id).count()
    if student_count:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"소속 학생 {student_count}명이 있어 삭제할 수 없습니다. 이용을 막으려면 '중지'로 변경하세요.",
        )
    key_count = (
        db.query(ApiKey)
        .filter(ApiKey.organization_id == org_id, ApiKey.status != "deleted")
        .count()
    )
    if key_count:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"발급된 API 키 {key_count}개가 있어 삭제할 수 없습니다. 키를 먼저 폐기하세요.",
        )
    # 빈 기관: organizations.id 를 참조하는 기관 스코프 행을 모두 정리한 뒤 기관을 지운다.
    # (classes·invitations·sites·captcha_settings 는 organizations 에 FK 가 걸려 있어
    #  남아 있으면 db.delete(org) 가 IntegrityError → 500 이 난다. 청구/사용로그도 함께 정리해 고아 방지.)
    # 자식→부모 순서로 삭제한다(예: api_usage_logs→api_keys→sites — api_keys.site_id FK).
    # 정리·삭제 전체를 한 트랜잭션으로 감싸, 예상 못 한 잔여 FK 로 IntegrityError 가 나면 500 이
    # 아니라 정직한 409 로 되돌린다(벌크 delete 는 즉시 실행돼 커밋 전에도 터질 수 있으므로).
    try:
        for model in (
            ApiUsageLog,
            ApiKey,
            CaptchaSetting,
            Site,
            Invitation,
            ClassRoom,
            Invoice,
            PaymentMethod,
            Membership,
            Subscription,
            User,
        ):
            db.query(model).filter(model.organization_id == org_id).delete(
                synchronize_session=False
            )
        db.query(OrgRegistrationRequest).filter(
            OrgRegistrationRequest.organization_id == org_id
        ).delete(synchronize_session=False)
        db.add(
            AuditLog(
                actor_user_id=principal.id,
                organization_id=None,  # 기관이 삭제되므로 FK 없는 참조로 남기지 않는다
                action="org.delete",
                target_type="organization",
                target_id=org_id,
                before_json={"name": org.name, "code": org.code},
            )
        )
        db.delete(org)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="아직 정리되지 않은 연결 데이터가 있어 삭제할 수 없습니다. 이용을 막으려면 '중지'로 변경하세요.",
        )
    return {"ok": True}


# ---------------------------------------------------------------- 운영자 계정 관리 (운영자)
# 플랫폼 운영자(ops) 계정은 공개 가입이 아니라 여기서만 만든다. 최초 운영자는 시드로 심고,
# 이후 운영자가 다른 운영자를 추가·중지할 수 있다(모든 변경은 감사 로그에 남음).
def _operator_row(u: User) -> dict:
    return {
        "id": u.id,
        "name": u.name,
        "email": u.email,
        "status": u.status,
        "two_factor_enabled": bool(u.two_factor_enabled),
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


class _OperatorCreateReq(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr


class _OperatorUpdateReq(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    status: str | None = Field(default=None, pattern="^(active|disabled)$")


@router.get("/operators")
def list_operators(principal: Principal = Depends(require_ops), db: Session = Depends(get_db)):
    rows = db.query(User).filter(User.role == "ops").order_by(User.created_at).all()
    return [_operator_row(u) for u in rows]


@router.post("/operators")
def create_operator(
    req: _OperatorCreateReq,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """운영자 계정 신규 발급 — 임시 비밀번호를 본인 이메일로 자동 통보하고, 첫 로그인 시 변경을 강제한다.

    운영자는 일반 로그인 폼이 아니라 숨겨진 /ops/login 에서만 인증된다(auth_service.ops_login).
    임시 비번은 응답에서도 1회 노출한다(이메일 dry-run/실패 시 수동 전달용).
    """
    email = req.email.strip().lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status.HTTP_409_CONFLICT, detail="이미 가입된 이메일입니다.")
    temp_password = secrets.token_urlsafe(9)
    op = User(
        email=email,
        password_hash=hash_password(temp_password),
        name=req.name,
        role="ops",
        status="active",
        email_verified_at=datetime.now(),  # 사용자 기록 시각 — 로컬(KST) 규약
        must_change_password=True,  # 첫 로그인 시 새 비번 강제 (전역 ForcePasswordGate)
    )
    db.add(op)
    db.flush()

    # 임시 비번을 본인 이메일로 자동 통보 (평문은 저장하지 않고 이 순간에만 발송)
    pw = escape(temp_password)
    html = (
        "<div style='font-family:sans-serif;line-height:1.7;color:#333'>"
        f"<p>{escape(op.name)}님, 안녕하세요. CatChap 운영팀입니다.</p>"
        "<p>CatChap <b>운영자 계정</b>이 발급되었습니다.</p>"
        "<div style='margin:16px 0;padding:14px 16px;background:#f1ecff;border-radius:10px'>"
        f"<b>로그인 이메일</b><br>{escape(op.email)}<br><br>"
        f"<b>임시 비밀번호</b><br>"
        f"<span style='font-size:18px;font-weight:700;color:#7a5bd6;letter-spacing:1px'>{pw}</span>"
        "</div>"
        "<p>운영자 전용 로그인 화면에서 접속하고, 보안을 위해 <b>첫 로그인 후 반드시 새 비밀번호로 변경</b>해 주세요.</p>"
        "<p>감사합니다. 🐾</p></div>"
    )
    sent = send_email(
        db, to_email=op.email, subject="[CatChap] 운영자 계정이 발급되었습니다", html=html, user_id=op.id
    )
    email_status = "dry_run" if not get_settings().smtp_enabled else ("sent" if sent else "failed")
    db.add(
        AuditLog(
            actor_user_id=principal.id,
            action="ops.operator_create",
            target_type="user",
            target_id=op.id,
            after_json={"name": op.name, "email": email, "email_status": email_status},
        )
    )
    db.commit()
    return {
        "ok": True,
        **_operator_row(op),
        "temp_password": temp_password,  # 이메일 실패/dry-run 시 수동 전달용 (1회 노출)
        "email_status": email_status,
    }


@router.post("/operators/{op_id}/reset-password")
def reset_operator_password(
    op_id: str,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """운영자 임시 비밀번호 재설정 — 새 임시 비번을 본인 이메일로 통보하고 첫 로그인 시 변경 강제.
    기존 세션(리프레시 토큰)은 즉시 폐기해 옛 비번 세션이 남지 않게 한다."""
    from app.services import auth_service

    op = db.get(User, op_id)
    if op is None or op.role != "ops":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="운영자를 찾을 수 없습니다.")
    temp_password = secrets.token_urlsafe(9)
    op.password_hash = hash_password(temp_password)
    op.must_change_password = True  # 첫 로그인 시 새 비번 강제
    auth_service.logout(db, op.id)  # 기존 모든 세션 폐기
    db.flush()
    pw = escape(temp_password)
    html = (
        "<div style='font-family:sans-serif;line-height:1.7;color:#333'>"
        f"<p>{escape(op.name or '운영자')}님, 안녕하세요. CatChap 운영팀입니다.</p>"
        "<p>운영자 계정의 <b>임시 비밀번호가 재설정</b>되었습니다.</p>"
        "<div style='margin:16px 0;padding:14px 16px;background:#f1ecff;border-radius:10px'>"
        f"<b>로그인 이메일</b><br>{escape(op.email)}<br><br><b>임시 비밀번호</b><br>"
        f"<span style='font-size:18px;font-weight:700;color:#7a5bd6;letter-spacing:1px'>{pw}</span></div>"
        "<p>운영자 전용 로그인 화면(주소창에 /ops/login 직접 입력)에서 접속하고, "
        "첫 로그인 후 <b>새 비밀번호로 변경</b>해 주세요.</p><p>감사합니다. 🐾</p></div>"
    )
    sent = send_email(
        db, to_email=op.email, subject="[CatChap] 운영자 임시 비밀번호 재설정", html=html, user_id=op.id
    )
    email_status = "dry_run" if not get_settings().smtp_enabled else ("sent" if sent else "failed")
    db.add(
        AuditLog(
            actor_user_id=principal.id,
            action="ops.operator_password_reset",
            target_type="user",
            target_id=op.id,
            after_json={"email": op.email, "email_status": email_status},
        )
    )
    db.commit()
    return {
        "ok": True,
        "name": op.name,
        "email": op.email,
        "temp_password": temp_password,  # 이메일 실패/dry-run 시 수동 전달용 (1회 노출)
        "email_status": email_status,
    }


@router.patch("/operators/{op_id}")
def update_operator(
    op_id: str,
    req: _OperatorUpdateReq,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """운영자 계정 수정 — 이름 변경 / 활성화·중지. 자기 잠금·전체 잠금은 막는다."""
    op = db.get(User, op_id)
    if op is None or op.role != "ops":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="운영자 계정을 찾을 수 없습니다.")
    if req.status == "disabled":
        if op_id == principal.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="자기 계정은 중지할 수 없어요.")
        active_ops = (
            db.query(User).filter(User.role == "ops", User.status == "active").count()
        )
        if op.status == "active" and active_ops <= 1:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="마지막 활성 운영자예요. 다른 운영자를 먼저 추가한 뒤 중지하세요.",
            )
    before = {"name": op.name, "status": op.status}
    if req.name is not None:
        op.name = req.name
    if req.status is not None:
        op.status = req.status
    db.add(
        AuditLog(
            actor_user_id=principal.id,
            action="ops.operator_update",
            target_type="user",
            target_id=op.id,
            before_json=before,
            after_json={"name": op.name, "status": op.status},
        )
    )
    db.commit()
    return _operator_row(op)


@router.get("/inquiries")
def inquiries(
    status_filter: str | None = None,
    search: str | None = None,  # 이름/이메일/소속/내용 부분일치
    page: int = 1,
    page_size: int = 50,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """문의하기 접수 목록 (status_filter: received|resolved, 없으면 전체). 최신순.

    문의는 제출마다 단조 증가하는 테이블 — 전량 반환 대신 서버 페이지네이션+검색.
    탭 배지용 상태별 건수(counts)는 검색 조건 적용 후 전체 기준으로 함께 반환한다.
    각 문의에 운영자 답변 스레드(replies)를 시간순으로 함께 반환한다.
    """
    page = max(1, page)
    page_size = max(1, min(200, page_size))

    base = db.query(Inquiry)
    if search:
        like = f"%{search.strip()}%"
        base = base.filter(
            Inquiry.name.like(like)
            | Inquiry.email.like(like)
            | Inquiry.affiliation.like(like)
            | Inquiry.content.like(like)
        )
    counts = {
        (st if st is not None else "unknown"): int(n)
        for st, n in base.with_entities(Inquiry.status, func.count(Inquiry.id))
        .group_by(Inquiry.status)
        .all()
    }
    q = base.order_by(Inquiry.created_at.desc())
    if status_filter:
        q = q.filter(Inquiry.status == status_filter)
    total = q.count()
    items = q.offset((page - 1) * page_size).limit(page_size).all()

    replies_by_inq: dict[str, list[InquiryReply]] = {}
    ids = [i.id for i in items]
    if ids:
        for rep in (
            db.query(InquiryReply)
            .filter(InquiryReply.inquiry_id.in_(ids))
            .order_by(InquiryReply.created_at)
            .all()
        ):
            replies_by_inq.setdefault(rep.inquiry_id, []).append(rep)

    return {
        "items": [
            {
                "id": i.id,
                "inquiry_type": i.inquiry_type,
                "name": i.name,
                "affiliation": i.affiliation,
                "email": i.email,
                "content": i.content,
                "status": i.status,
                "created_at": i.created_at.isoformat() if i.created_at else None,
                "replies": [
                    {
                        "id": rep.id,
                        "body": rep.body,
                        "answered_by": rep.answered_by,
                        "email_status": rep.email_status,
                        "created_at": rep.created_at.isoformat() if rep.created_at else None,
                    }
                    for rep in replies_by_inq.get(i.id, [])
                ],
            }
            for i in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "counts": {  # 탭 배지용 — 검색 조건 반영, status_filter 미반영(전 탭 공통)
            "received": counts.get("received", 0),
            "resolved": counts.get("resolved", 0),
            "all": sum(counts.values()),
        },
    }


class InquiryAnswerRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=5000)


@router.post("/inquiries/{inquiry_id}/answer")
def answer_inquiry(
    inquiry_id: str,
    req: InquiryAnswerRequest,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """문의에 답변 작성 → 문의자 이메일로 회신 + 답변 스레드에 누적 + resolved 처리.

    확인 후 여러 번 답변 가능(1문의 : N답변). SMTP 미설정(dry-run) 시 실제 발송 대신
    콘솔 출력이며, 답변 내용은 항상 DB에 보관된다.
    """
    i = db.query(Inquiry).filter(Inquiry.id == inquiry_id).first()
    if i is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="문의를 찾을 수 없습니다.")

    reply = InquiryReply(
        inquiry_id=i.id, body=req.answer, answered_by=principal.id, email_status="pending"
    )
    db.add(reply)
    i.status = "resolved"
    db.add(
        AuditLog(
            actor_user_id=principal.id,
            action="inquiry.answer",
            target_type="inquiry",
            target_id=i.id,
            # 감사 로그에서 '어떤 답변을 보냈는지' 미리보기로 볼 수 있게 답변 본문을 함께 남긴다.
            after_json={"answer": req.answer},
        )
    )
    db.commit()

    # 문의자 이메일로 회신 (HTML 인젝션 방지: 사용자 입력 escape 후 줄바꿈만 <br>)
    def _nl(s: str) -> str:
        return escape(s).replace("\n", "<br>")

    html = (
        "<div style='font-family:sans-serif;line-height:1.7;color:#333'>"
        f"<p>{escape(i.name)}님, 안녕하세요. CatChap 운영팀입니다.</p>"
        "<p>문의해 주신 내용에 대해 답변드립니다.</p>"
        "<div style='margin:16px 0;padding:14px 16px;background:#f6f6f8;border-radius:10px'>"
        f"<b>문의 내용</b><br>{_nl(i.content)}</div>"
        "<div style='margin:16px 0;padding:14px 16px;background:#fff3ee;border-radius:10px'>"
        f"<b>답변</b><br>{_nl(req.answer)}</div>"
        "<p>감사합니다. 🐾</p></div>"
    )
    sent = send_email(
        db, to_email=i.email, subject="[CatChap] 문의하신 내용에 대한 답변입니다", html=html
    )
    if not get_settings().smtp_enabled:
        reply.email_status = "dry_run"
    else:
        reply.email_status = "sent" if sent else "failed"
    db.commit()
    return {"ok": True, "status": "resolved", "email_sent": sent, "email_status": reply.email_status}


@router.post("/inquiries/{inquiry_id}/resolve")
def resolve_inquiry(
    inquiry_id: str,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """문의 처리 완료 (received → resolved). 감사 로그 기록."""
    i = db.query(Inquiry).filter(Inquiry.id == inquiry_id).first()
    if i is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="문의를 찾을 수 없습니다.")
    if i.status == "resolved":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="이미 처리된 문의입니다.")
    i.status = "resolved"
    db.add(
        AuditLog(
            actor_user_id=principal.id,
            action="inquiry.resolve",
            target_type="inquiry",
            target_id=i.id,
        )
    )
    db.commit()
    return {"ok": True, "status": "resolved"}


@router.get("/system")
def system(principal: Principal = Depends(require_ops), db: Session = Depends(get_db)):
    """시스템 상태 — 전부 실측. 가짜 상수를 반환하던 스텁을 재구현(0712).

    - db: SELECT 1 왕복 실측(ms)
    - captcha-engine: 인프로세스 문제은행 로드 확인 — 6과목 playable 문항 수
    - smtp: 설정 여부 + 최근 24시간 발송 성공/실패 실집계(email_logs)
    - disk: 컨테이너 루트 사용률
    - ai-server: 배포 보류 상태를 정직하게 표시(설정 자체가 없음)
    """
    import shutil
    import time as _time

    from sqlalchemy import text as _text

    from app.models import EmailLog
    from app.services import subject_banks

    services: list[dict] = []

    # DB — 실제 왕복시간
    t0 = _time.perf_counter()
    try:
        db.execute(_text("SELECT 1"))
        services.append({
            "name": "db", "status": "ok",
            "latency_ms": max(1, int((_time.perf_counter() - t0) * 1000)),
            "detail": "SELECT 1 왕복",
        })
    except Exception as e:  # noqa: BLE001 — 상태 보고가 목적, 어떤 예외든 error로
        services.append({"name": "db", "status": "error", "latency_ms": None,
                         "detail": type(e).__name__})

    # 캡차 엔진 — 문제은행이 실제로 로드돼 출제 가능한지
    t0 = _time.perf_counter()
    try:
        # 은행 레지스트리 키는 한국어 과목명(subject_banks.BANKS) — 영어 키로 조회하면 전부 0
        subjects = sorted(subject_banks.LIVE_SUBJECTS)
        counts = {s: len(subject_banks.playable_pool(s)) for s in subjects}
        total_playable = sum(counts.values())
        empty = [s for s, n in counts.items() if n == 0]
        services.append({
            "name": "captcha-engine",
            "status": "ok" if not empty else "degraded",
            "latency_ms": max(1, int((_time.perf_counter() - t0) * 1000)),
            "detail": f"출제 가능 {total_playable}문항"
                      + (f" · 빈 과목: {', '.join(empty)}" if empty else " · 6과목 정상"),
        })
    except Exception as e:  # noqa: BLE001
        services.append({"name": "captcha-engine", "status": "error", "latency_ms": None,
                         "detail": type(e).__name__})

    # SMTP — 설정 여부 + 최근 24시간 발송 결과 실집계
    smtp_on = get_settings().smtp_enabled
    day_ago = datetime.now() - timedelta(hours=24)
    mail_counts = {
        (st or "unknown"): int(n)
        for st, n in db.query(EmailLog.status, func.count(EmailLog.id))
        .filter(EmailLog.created_at >= day_ago)
        .group_by(EmailLog.status)
        .all()
    }
    failed = mail_counts.get("failed", 0)
    sent_ok = mail_counts.get("sent", 0)
    # 실패 1건(예: 문의자 이메일 오타)으로 전체 '주의'가 되면 과민 — 우리 발송 계통
    # 장애로 볼 신호(실패 5건 이상, 또는 실패가 있는데 성공이 0)일 때만 degraded.
    smtp_bad = failed >= 5 or (failed > 0 and sent_ok == 0)
    services.append({
        "name": "smtp",
        "status": ("degraded" if smtp_bad else "ok") if smtp_on else "dry-run",
        "latency_ms": None,
        "detail": (
            f"24시간: 발송 {mail_counts.get('sent', 0)} · 실패 {failed}"
            if smtp_on
            else "미설정 — 메일이 실발송되지 않음(dry-run)"
        ),
    })

    # 디스크 — 컨테이너 루트 사용률
    try:
        du = shutil.disk_usage("/")
        pct = round(du.used / du.total * 100, 1)
        services.append({
            "name": "disk",
            "status": "ok" if pct < 85 else "degraded",
            "latency_ms": None,
            "detail": f"사용 {pct}% ({du.used // 1024**3}GB / {du.total // 1024**3}GB)",
        })
    except OSError:
        services.append({"name": "disk", "status": "error", "latency_ms": None, "detail": None})

    # AI 서버 — 배포 보류 중(엔드포인트 설정 자체가 없음)을 정직하게 표시
    services.append({
        "name": "ai-server",
        "status": "not_deployed",
        "latency_ms": None,
        "detail": "행동 판정 모델 미배포 — 학습셋 구축 단계",
    })

    return {
        "services": services,
        "checked_at": datetime.now().isoformat(),  # KST(컨테이너 TZ) — 다른 시각과 동일 규약
    }


@router.get("/logs")
def logs(
    action: str | None = None,
    organization_id: str | None = None,
    date_from: str | None = None,  # 'YYYY-MM-DD' (해당일 00:00 포함)
    date_to: str | None = None,  # 'YYYY-MM-DD' (해당일 끝까지 포함)
    page: int = 1,
    page_size: int = 50,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """운영 감사로그 — 기관/행동/기간 필터 + 페이지네이션(실무 수준).

    운영자는 지원·보안·컴플라이언스 목적으로 여러 기관 활동을 교차 조회하되,
    아동 실명은 익명 코드로만 본다. 필터 없이 최근 50건만 보던 전역 피드에서
    기관별·행동별·기간별로 좁혀 볼 수 있도록 확장했다.
    """
    from datetime import date as _date, datetime as _dt, time as _time, timedelta as _td

    page = max(1, page)
    page_size = max(1, min(200, page_size))

    q = db.query(AuditLog)
    if action:
        q = q.filter(AuditLog.action == action)
    if organization_id:
        q = q.filter(AuditLog.organization_id == organization_id)
    # 기간: date_from 00:00 이상, date_to 다음날 00:00 미만(그날 끝까지 포함). 잘못된 형식은 무시.
    try:
        if date_from:
            q = q.filter(AuditLog.created_at >= _dt.combine(_date.fromisoformat(date_from), _time.min))
        if date_to:
            q = q.filter(AuditLog.created_at < _dt.combine(_date.fromisoformat(date_to) + _td(days=1), _time.min))
    except ValueError:
        pass

    total = q.count()
    rows = (
        q.order_by(AuditLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    # 실행자/기관을 사람이 읽을 수 있게 — '누가·언제·무엇을'의 '누가'가 UUID면 감사 로그 목적 미달
    actor_ids = {log.actor_user_id for log in rows if log.actor_user_id}
    org_ids = {log.organization_id for log in rows if log.organization_id}
    users = {u.id: u for u in db.query(User).filter(User.id.in_(list(actor_ids) or [""]))}
    students = {
        s.id: s
        for s in db.query(StudentProfile).filter(StudentProfile.id.in_(list(actor_ids) or [""]))
    }
    orgs = {o.id: o for o in db.query(Organization).filter(Organization.id.in_(list(org_ids) or [""]))}

    _salt = get_settings().JWT_SECRET_KEY

    def _actor(log: AuditLog) -> str | None:
        aid = log.actor_user_id
        if not aid:
            return None
        u = users.get(aid)
        if u is not None:
            role = {"ops": "운영자", "org_admin": "기관 관리자", "grade_head": "학년부장", "teacher": "교사", "parent": "학부모"}.get(u.role, u.role)
            return f"{u.name} ({role})"
        s = students.get(aid)
        if s is not None:
            # 학생은 익명(anon_code) — 운영자는 학생 식별정보(닉네임 포함)를 보지 않는다.
            code = hashlib.sha256(f"{_salt}:{s.id}".encode()).hexdigest()[:6].upper()
            return f"학생 {code}"
        return None

    def _actor_email(log: AuditLog) -> str | None:
        # 계정을 유일하게 식별하는 이메일 — 같은 이름의 운영자/기관 관리자가 여러 명이어도
        # 누구인지 구분할 수 있게 한다. 학생·삭제된 계정은 None(학생은 이메일 없음/익명 코드로 식별).
        u = users.get(log.actor_user_id) if log.actor_user_id else None
        return u.email if u else None

    # 문의 답변(inquiry.answer) 로그는 미리보기로 '원래 문의(질문)와 그 문의에 달린 모든 답변'을
    # 함께 보여준다(1문의 : N답변). 답변 스레드가 비어 있는 구(舊) 기록은 after_json 에 저장된
    # 답변이라도 복원한다.
    answer_inquiry_ids = {
        log.target_id for log in rows if log.action == "inquiry.answer" and log.target_id
    }
    inquiries_by_id: dict[str, Inquiry] = {}
    replies_by_inquiry: dict[str, list[InquiryReply]] = {}
    if answer_inquiry_ids:
        for inq in db.query(Inquiry).filter(Inquiry.id.in_(answer_inquiry_ids)).all():
            inquiries_by_id[inq.id] = inq
        for rep in (
            db.query(InquiryReply)
            .filter(InquiryReply.inquiry_id.in_(answer_inquiry_ids))
            .order_by(InquiryReply.created_at)
            .all()
        ):
            replies_by_inquiry.setdefault(rep.inquiry_id, []).append(rep)

    def _detail(log: AuditLog) -> dict | None:
        if log.action != "inquiry.answer" or not log.target_id:
            return None
        inq = inquiries_by_id.get(log.target_id)
        reps = replies_by_inquiry.get(log.target_id, [])
        answers = [
            {"body": r.body, "at": r.created_at.isoformat() if r.created_at else None}
            for r in reps
        ]
        # 구 기록 폴백: 답변 스레드가 비었으면 감사로그 after_json 에 저장된 답변이라도 보여준다.
        if not answers and log.after_json and log.after_json.get("answer"):
            answers = [
                {"body": log.after_json["answer"],
                 "at": log.created_at.isoformat() if log.created_at else None}
            ]
        if inq is None and not answers:
            return None
        return {
            "question": inq.content if inq else None,
            "question_by": inq.name if inq else None,
            "question_email": inq.email if inq else None,  # 회신용 — 문의 처리엔 필요
            "question_at": inq.created_at.isoformat() if inq and inq.created_at else None,
            "answers": answers,
        }

    items = [
        {
            "id": log.id,
            "action": log.action,
            "actor_user_id": log.actor_user_id,
            "actor_name": _actor(log),
            "actor_email": _actor_email(log),
            "organization_id": log.organization_id,
            "org_name": orgs[log.organization_id].name if log.organization_id in orgs else None,
            "target_type": log.target_type,
            "target_id": log.target_id,
            "detail": _detail(log),  # 문의 답변 미리보기용 본문 (그 외 액션은 None)
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in rows
    ]

    # 필터 드롭다운용 facet — 현재 페이지가 아니라 감사로그 전체에서 뽑는다(전역 선택지).
    action_facet = sorted(a for (a,) in db.query(AuditLog.action).distinct().all() if a)
    org_id_facet = [o for (o,) in db.query(AuditLog.organization_id).distinct().all() if o]
    facet_orgs = {
        o.id: o.name
        for o in db.query(Organization).filter(Organization.id.in_(org_id_facet or [""])).all()
    }
    org_facet = sorted(
        ({"id": oid, "name": facet_orgs.get(oid, oid)} for oid in org_id_facet),
        key=lambda x: x["name"],
    )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "actions": action_facet,  # 행동 필터 선택지
        "orgs": org_facet,  # 기관 필터 선택지 (id·이름)
    }


def _model_row(m: ModelVersion) -> dict:
    return {
        "id": m.id,
        "category": m.category,
        "name": m.name,
        "provider": m.provider,
        "version": m.version,
        "status": m.status,
        "description": m.description,
        "updated_on": m.updated_on,
    }


@router.get("/ai-models")
def ai_models(principal: Principal = Depends(require_ops), db: Session = Depends(get_db)):
    """모델 레지스트리 — 이 목록이 각 기관 콘솔 'AI 모델' 화면에 그대로 노출된다."""
    rows = db.query(ModelVersion).order_by(ModelVersion.created_at).all()
    return [_model_row(m) for m in rows]


class _ModelUpsertReq(BaseModel):
    category: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=100)
    provider: str = Field(min_length=1, max_length=60)
    version: str = Field(min_length=1, max_length=30)
    status: str = Field(pattern="^(정상|베타|점검|중단)$")
    description: str | None = Field(default=None, max_length=2000)


@router.post("/ai-models")
def ai_model_create(
    req: _ModelUpsertReq,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """모델 등록 — 기관 콘솔에 즉시 노출되므로 감사에 남긴다."""
    m = ModelVersion(
        category=req.category, name=req.name, provider=req.provider,
        version=req.version, status=req.status, description=req.description,
        updated_on=date.today().isoformat(),  # KST 날짜 — 기관 화면 '업데이트' 표기
    )
    db.add(m)
    db.flush()
    db.add(
        AuditLog(
            actor_user_id=principal.id, organization_id=None,
            action="ops.ai_model_create", target_type="model_version", target_id=m.id,
            after_json={"name": req.name, "version": req.version, "status": req.status},
        )
    )
    db.commit()
    return {"ok": True, **_model_row(m)}


@router.patch("/ai-models/{model_id}")
def ai_model_update(
    model_id: str,
    req: _ModelUpsertReq,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """모델 정보 수정(이름/버전/상태/설명) — 기관 콘솔 표시가 바로 바뀐다."""
    m = db.get(ModelVersion, model_id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="모델을 찾을 수 없습니다.")
    before = {"name": m.name, "version": m.version, "status": m.status}
    m.category = req.category
    m.name = req.name
    m.provider = req.provider
    m.version = req.version
    m.status = req.status
    m.description = req.description
    m.updated_on = date.today().isoformat()
    db.add(
        AuditLog(
            actor_user_id=principal.id, organization_id=None,
            action="ops.ai_model_update", target_type="model_version", target_id=m.id,
            before_json=before,
            after_json={"name": req.name, "version": req.version, "status": req.status},
        )
    )
    db.commit()
    return {"ok": True, **_model_row(m)}


# ---------------------------------------------------------------- 기관 가입 승인
def _req_row(r: OrgRegistrationRequest, db: Session) -> dict:
    org = (
        db.query(Organization).filter(Organization.id == r.organization_id).first()
        if r.organization_id
        else None
    )
    return {
        "id": r.id,
        "org_name": r.org_name,
        "org_type": r.org_type,
        "business_number": r.business_number,
        "address": r.address,
        "contact_name": r.contact_name,
        "contact_email": r.contact_email,
        "contact_phone": r.contact_phone,
        "expected_students": r.expected_students,
        "plan_interest": r.plan_interest,
        "status": r.status,
        "org_code": org.code if org else None,
        "org_status": org.status if org else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "approved_at": r.approved_at.isoformat() if r.approved_at else None,
    }


@router.get("/registration-requests")
def registration_requests(
    status_filter: str | None = None,
    page: int | None = None,  # 없으면 기존 배열(하위호환), 있으면 페이지 응답 + 탭 배지 counts
    page_size: int = 50,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """기관 가입 신청 목록 (status_filter: pending|approved|rejected, 없으면 전체).

    신청은 승인/거절 후에도 기록이 남아 단조 증가 — 페이지 응답에는 탭 배지용
    상태별 counts(전체 기준, status_filter 미반영)를 함께 준다.
    """
    base = db.query(OrgRegistrationRequest)
    q = base.order_by(OrgRegistrationRequest.created_at.desc())
    if status_filter:
        q = q.filter(OrgRegistrationRequest.status == status_filter)
    if page is None:
        return [_req_row(r, db) for r in q.all()]  # 하위호환
    counts = {
        (st or "unknown"): int(n)
        for st, n in base.with_entities(
            OrgRegistrationRequest.status, func.count(OrgRegistrationRequest.id)
        )
        .group_by(OrgRegistrationRequest.status)
        .all()
    }
    total = q.count()
    page = max(1, page)
    page_size = max(1, min(200, page_size))
    rows = q.offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": [_req_row(r, db) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "counts": {
            "pending": counts.get("pending", 0),
            "approved": counts.get("approved", 0),
            "rejected": counts.get("rejected", 0),
        },
    }


@router.post("/registration-requests/{request_id}/approve")
def approve_request(
    request_id: str,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """승인: 신청 approved + 기관/관리자 멤버십 active 전환 → 로그인·이용 가능."""
    r = db.query(OrgRegistrationRequest).filter(OrgRegistrationRequest.id == request_id).first()
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="신청을 찾을 수 없습니다.")
    if r.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"이미 처리된 신청입니다({r.status}).")
    r.status = "approved"
    r.approved_at = datetime.now()  # 승인 시각은 화면 노출 — 로컬(KST) 규약
    # 승인 시 관리자 계정 자격증명 발급 — 신청 단계엔 비번이 없다(신청서 흐름).
    # email_verified_at 이 None 인 org_admin = 미발급 계정 → 임시 비번 발급 + active 전환.
    # 임시 비번은 이 응답에서만 1회 노출된다(이후 조회 불가) — 운영자가 담당자에게 전달.
    issued_admins: list[dict] = []
    if r.organization_id:
        org = db.query(Organization).filter(Organization.id == r.organization_id).first()
        if org:
            org.status = "active"
        for m in (
            db.query(Membership)
            .filter(Membership.organization_id == r.organization_id, Membership.status == "pending")
            .all()
        ):
            m.status = "active"
        for u in (
            db.query(User)
            .filter(User.organization_id == r.organization_id, User.role == "org_admin")
            .all()
        ):
            u.status = "active"
            if u.email_verified_at is None:  # 신청서 흐름으로 만든 미발급 계정
                temp_password = secrets.token_urlsafe(9)
                u.password_hash = hash_password(temp_password)
                u.email_verified_at = datetime.now()  # 운영자 승인으로 이메일 소유 확인 갈음(KST)
                u.must_change_password = True  # 임시 비번 첫 로그인 시 강제 변경
                issued_admins.append(
                    {"email": u.email, "temp_password": temp_password, "organization_id": org.id}
                )
    db.add(
        AuditLog(
            actor_user_id=principal.id,
            organization_id=r.organization_id,
            action="org_registration_approved",
            target_type="org_registration_request",
            target_id=r.id,
        )
    )
    db.commit()
    return {"ok": True, "status": "approved", "admin_credentials": issued_admins}


class _SendCredReq(BaseModel):
    email: EmailStr
    temp_password: str


@router.post("/orgs/{org_id}/send-admin-credentials")
def send_admin_credentials(
    org_id: str,
    req: _SendCredReq,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """승인 시 발급한 관리자 임시 비밀번호를 담당자 이메일로 발송.

    평문 비번은 저장하지 않는다(승인 응답에서만 1회 노출) → 방금 발급한 값을
    클라이언트가 되돌려주면 계정 해시와 대조해 '현재 유효한 비번'일 때만 발송한다.
    이렇게 하면 오래된/조작된 값을 잘못 메일로 보내는 일을 막는다.
    """
    email = req.email.strip().lower()
    user = (
        db.query(User)
        .filter(User.organization_id == org_id, User.email == email, User.role == "org_admin")
        .first()
    )
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="관리자 계정을 찾을 수 없습니다.")
    if not verify_password(req.temp_password, user.password_hash):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="임시 비밀번호가 현재 계정과 일치하지 않아요. 승인 직후 화면에서만 발송할 수 있어요.",
        )
    org = db.query(Organization).filter(Organization.id == org_id).first()
    org_name = escape(org.name) if org else "기관"
    pw = escape(req.temp_password)
    html = (
        "<div style='font-family:sans-serif;line-height:1.7;color:#333'>"
        f"<p>{escape(user.name)}님, 안녕하세요. CatChap 운영팀입니다.</p>"
        f"<p><b>{org_name}</b>의 기관 등록 신청이 승인되어 관리자 계정이 발급되었습니다.</p>"
        "<div style='margin:16px 0;padding:14px 16px;background:#fff3ee;border-radius:10px'>"
        f"<b>로그인 이메일</b><br>{escape(user.email)}<br><br>"
        f"<b>임시 비밀번호</b><br>"
        f"<span style='font-size:18px;font-weight:700;color:#e85b2a;letter-spacing:1px'>{pw}</span>"
        "</div>"
        "<p>보안을 위해 <b>첫 로그인 후 반드시 새 비밀번호로 변경</b>해 주세요.</p>"
        "<p>감사합니다. 🐾</p></div>"
    )
    sent = send_email(
        db, to_email=user.email, subject="[CatChap] 기관 관리자 계정이 발급되었습니다", html=html
    )
    email_status = "dry_run" if not get_settings().smtp_enabled else ("sent" if sent else "failed")
    db.add(
        AuditLog(
            actor_user_id=principal.id,
            organization_id=org_id,
            action="org.admin_credentials_sent",
            target_type="user",
            target_id=user.id,
            after_json={"to": user.email, "email_status": email_status},
        )
    )
    db.commit()
    return {"ok": True, "email_sent": sent, "email_status": email_status, "to": user.email}


@router.post("/registration-requests/{request_id}/reject")
def reject_request(
    request_id: str,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """거절: 신청 rejected + 기관 disabled."""
    r = db.query(OrgRegistrationRequest).filter(OrgRegistrationRequest.id == request_id).first()
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="신청을 찾을 수 없습니다.")
    if r.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"이미 처리된 신청입니다({r.status}).")
    r.status = "rejected"
    if r.organization_id:
        org = db.query(Organization).filter(Organization.id == r.organization_id).first()
        if org:
            org.status = "disabled"
    db.add(
        AuditLog(
            actor_user_id=principal.id,
            organization_id=r.organization_id,
            action="org_registration_rejected",
            target_type="org_registration_request",
            target_id=r.id,
        )
    )
    db.commit()
    return {"ok": True, "status": "rejected"}


# ---------------------------------------------------------------- 캡차 API 키 관리 (운영자)
class _IssueKeyReq(BaseModel):
    organization_id: str
    product: str = Field(pattern="^(captcha|edu)$")
    subject: str | None = None
    label: str | None = Field(default=None, max_length=100)
    domain: str | None = Field(default=None, max_length=255)
    # 우리 인앱 키(과목 전환 허용)면 True. 외부 판매 키는 False(발급 과목 고정) — 기본값.
    first_party: bool = False


def _apikey_row(db: Session, k: ApiKey, usage: int = 0) -> dict:
    org = db.get(Organization, k.organization_id)
    plan = _cs.plan_for_org(db, k.organization_id)
    return {
        "id": k.id,
        "organization_id": k.organization_id,
        "organization_name": org.name if org else None,
        "product": k.product,
        "product_name": _cs.PRODUCTS.get(k.product, k.product),
        "subject": k.subject,
        "label": k.label,
        "first_party": k.first_party,
        "site_key": k.site_key,  # 공개키 — 목록 노출 OK
        "status": k.status,
        "usage_month": usage,  # 이번 달 이 키의 challenge 발급 수
        "plan": plan.name if plan else "미구독",
        "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
        "created_at": k.created_at.isoformat() if k.created_at else None,
    }


@router.get("/plans")
def ops_plans(principal: Principal = Depends(require_ops), db: Session = Depends(get_db)):
    """요금제 목록 + 제품 허용 범위 (키 발급 시 참고)."""
    plans = db.query(Plan).order_by(Plan.monthly_price).all()
    return {
        "products": _cs.PRODUCTS,
        "edu_subjects": _cs.EDU_SUBJECTS,
        "plans": [
            {
                "key": p.key, "name": p.name, "monthly_price": p.monthly_price,
                "api_quota": p.api_quota,
                "products": _cs.PLAN_PRODUCTS.get(p.key, _cs.DEFAULT_PRODUCTS),
            }
            for p in plans
        ],
    }


@router.get("/api-keys")
def ops_list_api_keys(
    organization_id: str | None = None,
    page: int | None = None,  # 없으면 기존 배열(하위호환), 있으면 페이지 응답
    page_size: int = 50,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    q = db.query(ApiKey).filter(ApiKey.status != "deleted")
    if organization_id:
        q = q.filter(ApiKey.organization_id == organization_id)
    total = q.count()
    q = q.order_by(ApiKey.created_at.desc())
    if page is not None:
        page = max(1, page)
        page_size = max(1, min(200, page_size))
        q = q.offset((page - 1) * page_size).limit(page_size)
    rows = q.all()
    # 키별 이번 달 challenge 호출 수(전 기관 한 번에)
    # created_at은 로컬(KST) — UTC 월초로 자르면 한국 월초 첫 9시간에 전월이 섞인다
    first = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    usage = dict(
        db.query(ApiUsageLog.api_key_id, func.count(ApiUsageLog.id))
        .filter(
            ApiUsageLog.created_at >= first,
            ApiUsageLog.endpoint.like("%challenge%"),
            ApiUsageLog.api_key_id.isnot(None),
        )
        .group_by(ApiUsageLog.api_key_id)
        .all()
    )
    items = [_apikey_row(db, k, usage.get(k.id, 0)) for k in rows]
    if page is None:
        return items  # 하위호환
    active_total = (
        db.query(func.count(ApiKey.id)).filter(ApiKey.status == "active").scalar() or 0
    )
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "active_total": int(active_total),  # 헤더 요약용 — 전체 활성 키 수(필터 무관)
    }


@router.post("/api-keys")
def ops_issue_api_key(
    req: _IssueKeyReq, principal: Principal = Depends(require_ops), db: Session = Depends(get_db)
):
    """캡차/교육형 API 키 발급 — 기관 요금제가 그 제품을 허용해야 발급 가능. secret은 1회 노출."""
    org = db.get(Organization, req.organization_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="기관을 찾을 수 없습니다.")
    plan = _cs.plan_for_org(db, req.organization_id)
    if req.product not in _cs.allowed_products(plan):
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"{org.name}의 요금제({plan.name if plan else '미구독'})로는 '{_cs.PRODUCTS[req.product]}'를 발급할 수 없어요.",
        )
    issued = _cs.issue_key(
        db, org_id=req.organization_id, product=req.product, subject=req.subject,
        label=req.label, domain=req.domain, created_by=principal.id,
        first_party=req.first_party,
    )
    # 판매 프로비저닝: edu 키를 발급하면 그 과목을 기관 구매목록(edu_subjects)에 자동 반영 →
    # 이후 기관 관리자가 그 과목 키를 셀프 발급할 수 있게 된다.
    if req.product == "edu" and req.subject:
        subs = list(org.edu_subjects or [])
        if req.subject not in subs:
            org.edu_subjects = subs + [req.subject]
    db.add(
        AuditLog(
            actor_user_id=principal.id, organization_id=req.organization_id,
            action="captcha.api_key_issue", target_type="api_key", target_id=issued["id"],
            after_json={"product": req.product, "subject": req.subject, "label": req.label,
                        "first_party": req.first_party},
        )
    )
    db.commit()
    # secret_key 는 이 응답에서만 노출
    return {"ok": True, **issued}


@router.delete("/api-keys/{key_id}")
def ops_revoke_api_key(
    key_id: str, principal: Principal = Depends(require_ops), db: Session = Depends(get_db)
):
    k = db.get(ApiKey, key_id)
    if k is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="키를 찾을 수 없습니다.")
    k.status = "disabled"
    db.add(
        AuditLog(
            actor_user_id=principal.id, organization_id=k.organization_id,
            action="captcha.api_key_revoke", target_type="api_key", target_id=k.id,
        )
    )
    db.commit()
    return {"ok": True}


@router.post("/api-keys/{key_id}/rotate-secret")
def ops_rotate_secret(
    key_id: str, principal: Principal = Depends(require_ops), db: Session = Depends(get_db)
):
    """secret_key 재발급(유출 대응) — site_key 유지. secret 1회 노출."""
    k = db.get(ApiKey, key_id)
    if k is None or k.status == "deleted":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="키를 찾을 수 없습니다.")
    secret = _cs.rotate_secret(db, k)
    db.add(
        AuditLog(
            actor_user_id=principal.id, organization_id=k.organization_id,
            action="captcha.api_key_rotate", target_type="api_key", target_id=k.id,
        )
    )
    db.commit()
    return {"ok": True, "site_key": k.site_key, "secret_key": secret}


class _EntitlementReq(BaseModel):
    edu_subjects: list[str] = Field(default_factory=list)


@router.patch("/orgs/{org_id}/entitlements")
def ops_set_org_entitlements(
    org_id: str, req: _EntitlementReq,
    principal: Principal = Depends(require_ops), db: Session = Depends(get_db),
):
    """기관이 구매한 교육형 과목(edu_subjects) 설정 — 판매 프로비저닝. 기관 관리자는
    이 범위 안에서만 키를 셀프 발급할 수 있다."""
    org = db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="기관을 찾을 수 없습니다.")
    subs = [s for s in req.edu_subjects if s in _cs.EDU_SUBJECTS]
    before = list(org.edu_subjects or [])
    org.edu_subjects = subs
    db.add(
        AuditLog(
            actor_user_id=principal.id, organization_id=org_id,
            action="org.entitlements_set", target_type="organization", target_id=org_id,
            before_json={"edu_subjects": before}, after_json={"edu_subjects": subs},
        )
    )
    db.commit()
    return {"ok": True, "edu_subjects": subs}


# ---------------------------------------------------------------- 행동 데이터 (아동용 캡차 학습셋)
# interaction_result 값이 수집 경로에 따라 갈린다:
# 교육형 API(record_behavior)는 correct|incorrect, 인앱 게임(seed 포함)은 pass|fail.
_BEHAVIOR_PASS = ("correct", "pass")
_BEHAVIOR_FAIL = ("incorrect", "fail")


def _behavior_group_metrics(db: Session, group: str, *filters) -> dict:
    """그룹(아동/익명)별 행동 지표 평균 — 아동·성인 행동이 갈라지는지 보는 비교 데이터.

    평균 속도는 상한(AVG_SPEED_CAP=100 px/ms)에 걸린 자기신고 값을 제외한다 —
    단위가 어긋난 클라이언트 신고분이 섞이면 그룹 평균이 통째로 오염된다.
    """
    from sqlalchemy import case

    row = (
        db.query(
            func.count(BehaviorSummary.id),
            func.avg(BehaviorSummary.solve_time_ms),
            func.avg(BehaviorSummary.path_length),
            func.avg(case((BehaviorSummary.avg_speed < 100, BehaviorSummary.avg_speed))),
            func.avg(BehaviorSummary.pause_count),
            func.avg(BehaviorSummary.retry_count),
        )
        .filter(*filters)
        .one()
    )

    def _r(v, nd: int):
        return round(float(v), nd) if v is not None else None

    return {
        "group": group,
        "count": int(row[0] or 0),
        "avg_solve_time_ms": _r(row[1], 0),
        "avg_path_length": _r(row[2], 1),
        "avg_speed": _r(row[3], 2),
        "avg_pause_count": _r(row[4], 1),
        "avg_retry_count": _r(row[5], 1),
    }


@router.get("/behavior/overview")
def behavior_overview(
    principal: Principal = Depends(require_ops), db: Session = Depends(get_db)
):
    """행동 데이터 수집 현황 — 아동용 캡차 판정 모델 학습셋 구축의 기초 지표.

    핵심은 '아동(학생 계정 연결)' vs '익명(외부 임베드, 성인 포함 추정)' 그룹의
    행동 지표 비교 — 같은 과제에서 두 그룹이 실제로 갈라지는지 보여준다.
    """
    total = db.query(BehaviorSummary).count()
    # created_at은 로컬 시각(app/db/base.py) — UTC(_now)로 빼면 9시간 과대 집계됨
    week_ago = datetime.now() - timedelta(days=7)
    week_count = db.query(BehaviorSummary).filter(BehaviorSummary.created_at >= week_ago).count()

    def _group_counts(col) -> dict:
        return {
            (k if k is not None else "unknown"): int(n)
            for k, n in db.query(col, func.count(BehaviorSummary.id)).group_by(col).all()
        }

    # 시간대(0~23시) 분포 — 최근 7일, created_at은 KST라 그대로 시(hour) 추출.
    # 봇 트래픽은 특정 시각(특히 심야)에 몰리는 경향 — 아동 트래픽과 갈라지는 축.
    hourly = [0] * 24
    for (ca,) in (
        db.query(BehaviorSummary.created_at)
        .filter(BehaviorSummary.created_at >= week_ago)
        .all()
    ):
        if ca is not None:
            hourly[ca.hour] += 1

    # 풀이시간 히스토그램 — 사람/봇 군집이 갈라지는 분포 축. 경계(ms): 아동 보정
    # 스코어링 임계값(800/1500/3000)과 정렬해 구간별 위험 의미가 읽히게 한다.
    solve_edges = [0, 800, 1500, 3000, 5000, 10000, 30000]
    solve_hist = [0] * (len(solve_edges))  # 마지막 칸 = 30s 초과
    for (ms,) in db.query(BehaviorSummary.solve_time_ms).all():
        v = ms or 0
        for i in range(len(solve_edges) - 1, -1, -1):
            if v >= solve_edges[i]:
                solve_hist[i] += 1
                break

    return {
        "total": total,
        "week_count": week_count,
        "trace_count": db.query(BehaviorTrace).count(),  # 원시 궤적이 남은 레코드 수
        "by_source": _group_counts(BehaviorSummary.source_type),
        "by_result": _group_counts(BehaviorSummary.interaction_result),
        "by_risk": _group_counts(BehaviorSummary.risk_level),
        "by_dataset": _group_counts(BehaviorSummary.dataset_status),
        "by_label": _group_counts(BehaviorSummary.sample_label),  # organic|bot|human
        "hourly_week": hourly,  # 최근 7일 KST 시간대(0~23시)별 수집 건수
        "solve_hist": {  # 풀이시간 분포 (구간 시작 ms → 건수)
            "edges_ms": solve_edges,
            "counts": solve_hist,
        },
        "comparison": [
            _behavior_group_metrics(db, "child", BehaviorSummary.student_id.isnot(None)),
            _behavior_group_metrics(db, "anonymous", BehaviorSummary.student_id.is_(None)),
        ],
    }


def _trace_preview(points: list, cap: int = 24) -> list[list[float]] | None:
    """원시 궤적을 목록 인라인 스파크라인용으로 다운샘플 — [x, y] 정규화 좌표만, 최대 cap개.

    목록 한 페이지(최대 200행)의 각 궤적 전체 좌표(최대 2000점)를 그대로 내려보내면
    응답이 과대해지므로, 시작·끝을 보존하며 균등 간격으로 줄인다. t(시간)는 뺀다.
    """
    if not points:
        return None
    n = len(points)
    if n <= cap:
        idxs = range(n)
    else:
        # 시작(0)과 끝(n-1)을 포함하도록 균등 샘플
        step = (n - 1) / (cap - 1)
        idxs = sorted({int(round(i * step)) for i in range(cap)} | {0, n - 1})
    out: list[list[float]] = []
    for i in idxs:
        p = points[i]
        if len(p) >= 3:
            out.append([round(float(p[1]), 4), round(float(p[2]), 4)])
    return out or None


@router.get("/behavior/records")
def behavior_records(
    source: str | None = None,
    result_filter: str | None = None,  # pass|fail (correct/pass·incorrect/fail 통합)
    risk: str | None = None,  # low|review|elevated
    group: str | None = None,  # student|anonymous
    dataset: str | None = None,  # candidate|included|excluded
    label: str | None = None,  # organic|bot|human (지도학습 라벨)
    date_from: str | None = None,  # 'YYYY-MM-DD' KST (해당일 00:00 포함)
    date_to: str | None = None,  # 'YYYY-MM-DD' KST (해당일 끝까지 포함)
    limit: int = 50,
    offset: int = 0,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """행동 데이터 레코드 목록 (필터 + 페이지네이션, 최신순)."""
    from datetime import date as _date, time as _time

    q = db.query(BehaviorSummary)
    if source:
        q = q.filter(BehaviorSummary.source_type == source)
    if result_filter == "pass":
        q = q.filter(BehaviorSummary.interaction_result.in_(_BEHAVIOR_PASS))
    elif result_filter == "fail":
        q = q.filter(BehaviorSummary.interaction_result.in_(_BEHAVIOR_FAIL))
    if risk:
        q = q.filter(BehaviorSummary.risk_level == risk)
    if group == "student":
        q = q.filter(BehaviorSummary.student_id.isnot(None))
    elif group == "anonymous":
        q = q.filter(BehaviorSummary.student_id.is_(None))
    if dataset:
        q = q.filter(BehaviorSummary.dataset_status == dataset)
    if label:
        q = q.filter(BehaviorSummary.sample_label == label)
    # 기간 — created_at은 로컬(KST) naive라 날짜 경계도 로컬 자정. 잘못된 형식은 무시.
    try:
        if date_from:
            q = q.filter(
                BehaviorSummary.created_at
                >= datetime.combine(_date.fromisoformat(date_from), _time.min)
            )
        if date_to:
            q = q.filter(
                BehaviorSummary.created_at
                < datetime.combine(_date.fromisoformat(date_to) + timedelta(days=1), _time.min)
            )
    except ValueError:
        pass

    total = q.count()
    limit = max(1, min(200, limit))
    rows = (
        # id 보조 정렬: created_at 동률(초 단위) 시 offset 페이지 경계 중복/누락 방지
        q.order_by(BehaviorSummary.created_at.desc(), BehaviorSummary.id.desc())
        .offset(max(0, offset))
        .limit(limit)
        .all()
    )

    # 학생/기관 이름·궤적 유무 일괄 조회 (행별 N+1 방지)
    sids = {r.student_id for r in rows if r.student_id}
    students = (
        {s.id: s for s in db.query(StudentProfile).filter(StudentProfile.id.in_(sids)).all()}
        if sids
        else {}
    )
    oids = {r.organization_id for r in rows}
    orgs = (
        {o.id: o for o in db.query(Organization).filter(Organization.id.in_(oids)).all()}
        if oids
        else {}
    )
    ids = [r.id for r in rows]
    trace_points: dict[str, int] = {}
    trace_previews: dict[str, list] = {}
    if ids:
        for bid, pc, pts in (
            db.query(BehaviorTrace.behavior_id, BehaviorTrace.point_count, BehaviorTrace.points)
            .filter(BehaviorTrace.behavior_id.in_(ids))
            .all()
        ):
            trace_points[bid] = pc
            preview = _trace_preview(pts)
            if preview:
                trace_previews[bid] = preview

    # 아동 PII 비노출: 운영자에게는 닉네임·학생코드·정확나이 대신 익명 코드만 내려준다.
    # JWT 시크릿을 소금으로 쓴 해시라 감사로그 등 다른 화면의 ID와 대조해도 특정 불가,
    # 같은 학생은 항상 같은 코드라 학습셋 큐레이션(동일인 묶기)은 유지된다.
    _salt = get_settings().JWT_SECRET_KEY

    def _anon_code(student_id: str) -> str:
        return hashlib.sha256(f"{_salt}:{student_id}".encode()).hexdigest()[:6].upper()

    def _row(r: BehaviorSummary) -> dict:
        s = students.get(r.student_id) if r.student_id else None
        org = orgs.get(r.organization_id)
        return {
            "id": r.id,
            "source_type": r.source_type,
            "organization_name": org.name if org else None,
            "student": {
                "anon_code": _anon_code(s.id),
                "grade_band": s.grade_band,
            }
            if s
            else None,
            "solve_time_ms": r.solve_time_ms,
            "path_length": r.path_length,
            "avg_speed": r.avg_speed,
            "pause_count": r.pause_count,
            "retry_count": r.retry_count,
            "drop_distance_norm": r.drop_distance_norm,
            "interaction_result": r.interaction_result,
            "risk_level": r.risk_level,
            "input_type": r.input_type,
            "sample_label": r.sample_label,
            "dataset_status": r.dataset_status,
            "trace_points": trace_points.get(r.id),  # None = 원시 궤적 없음
            "trace_preview": trace_previews.get(r.id),  # 인라인 스파크라인용 [x,y] (없으면 None)
            "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }

    return {"total": total, "items": [_row(r) for r in rows]}


@router.get("/behavior/records/{record_id}/trace")
def behavior_trace(
    record_id: str,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """레코드의 원시 포인터 궤적 — 목록에서 궤적 뱃지 클릭 시 시각화용."""
    t = db.query(BehaviorTrace).filter(BehaviorTrace.behavior_id == record_id).first()
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="이 레코드에는 궤적이 없습니다.")
    return {
        "behavior_id": t.behavior_id,
        "points": t.points,
        "point_count": t.point_count,
        "duration_ms": t.duration_ms,
        "box_w": t.box_w,
        "box_h": t.box_h,
    }


# ---------------------------------------------------------------- 외부 업체 제공용 익명 내보내기
K_ANON_MIN = 5  # 집계 소집단 최소 고유 학생 수 — 이 미만 그룹은 제외(단독 재식별 방지)


@router.get("/behavior/export")
def behavior_export(
    mode: str = "aggregate",  # aggregate(집계·개인0·k익명) | rows(행단위 가명)
    fmt: str = "csv",  # csv | json
    dataset: str = "included",  # dataset_status 필터: included(큐레이션됨) | candidate | all
    source_type: str | None = None,
    risk: str | None = None,  # low|review|elevated
    result_filter: str | None = None,  # pass|fail
    date_from: str | None = None,  # 'YYYY-MM-DD' KST
    date_to: str | None = None,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """외부 업체(학습지사)에 넘길 **익명** 행동데이터 내보내기 — 학교명·학생 식별정보 전부 제거.

    - aggregate: 집단 통계만(개인 0건). k-익명성(고유 학생 K_ANON_MIN 미만 집단 제외)로 소집단
      단독 재식별 차단. **외부 판매에 가장 안전.**
    - rows: 행 단위. 학생은 가명(anon_code, 외부는 재식별 불가)·나이대·성별·날짜(정확시각 제거)만.
      모델 학습용. anon_code는 가명이므로 재식별 금지 계약(DUA)이 전제.

    내보낼 때마다 감사로그(behavior.export)를 남긴다.
    """
    if mode not in ("aggregate", "rows"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="mode는 aggregate|rows.")
    if fmt not in ("csv", "json"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="fmt는 csv|json.")

    q = db.query(BehaviorSummary).filter(BehaviorSummary.student_id.isnot(None))
    if dataset in ("included", "candidate", "excluded"):
        q = q.filter(BehaviorSummary.dataset_status == dataset)
    if source_type:
        q = q.filter(BehaviorSummary.source_type == source_type)
    # 콘솔 필터와 동일 축의 내보내기 축소 — 위험도/결과/기간
    if risk in ("low", "review", "elevated"):
        q = q.filter(BehaviorSummary.risk_level == risk)
    if result_filter == "pass":
        q = q.filter(BehaviorSummary.interaction_result.in_(_BEHAVIOR_PASS))
    elif result_filter == "fail":
        q = q.filter(BehaviorSummary.interaction_result.in_(_BEHAVIOR_FAIL))
    try:
        from datetime import date as _date, time as _time

        if date_from:
            q = q.filter(
                BehaviorSummary.created_at
                >= datetime.combine(_date.fromisoformat(date_from), _time.min)
            )
        if date_to:
            q = q.filter(
                BehaviorSummary.created_at
                < datetime.combine(_date.fromisoformat(date_to) + timedelta(days=1), _time.min)
            )
    except ValueError:
        pass
    rows = q.all()

    sids = {r.student_id for r in rows}
    students = (
        {s.id: s for s in db.query(StudentProfile).filter(StudentProfile.id.in_(sids)).all()}
        if sids
        else {}
    )
    _salt = get_settings().JWT_SECRET_KEY

    def _anon(sid: str) -> str:  # 가명(salted 해시) — 시크릿 없는 외부는 되돌릴 수 없음
        return hashlib.sha256(f"{_salt}:{sid}".encode()).hexdigest()[:6].upper()

    def _gb(s):
        return (s.grade_band if s else None) or "unknown"

    def _gd(s):
        return (s.gender if s and s.gender else None) or "unknown"

    k_dropped = 0
    if mode == "rows":
        cols = [
            "anon_code", "grade_band", "gender", "source_type", "input_type",
            "interaction_result", "risk_level", "sample_label", "solve_time_ms",
            "path_length", "avg_speed", "pause_count", "retry_count",
            "drop_distance_norm", "date",
        ]
        records = []
        for r in rows:
            s = students.get(r.student_id)
            when = r.occurred_at or r.created_at
            records.append({
                "anon_code": _anon(r.student_id),  # 가명 — 학교명·실ID 없음
                "grade_band": _gb(s),
                "gender": _gd(s),
                "source_type": r.source_type,
                "input_type": r.input_type,
                "interaction_result": r.interaction_result,
                "risk_level": r.risk_level,
                "sample_label": r.sample_label,
                "solve_time_ms": r.solve_time_ms,
                "path_length": r.path_length,
                "avg_speed": r.avg_speed,
                "pause_count": r.pause_count,
                "retry_count": r.retry_count,
                "drop_distance_norm": r.drop_distance_norm,
                "date": when.date().isoformat() if when else None,  # 정확 시각 제거·날짜만
            })
    else:  # aggregate — 집단 통계(개인 0) + k-익명성
        cols = [
            "grade_band", "gender", "source_type", "input_type", "n_events",
            "n_students", "avg_solve_time_ms", "avg_path_length", "avg_pause_count",
            "correct_rate",
        ]
        groups: dict = defaultdict(
            lambda: {"n": 0, "uids": set(), "solve": 0.0, "path": 0.0, "pause": 0.0, "correct": 0}
        )
        for r in rows:
            s = students.get(r.student_id)
            key = (_gb(s), _gd(s), r.source_type, r.input_type or "unknown")
            g = groups[key]
            g["n"] += 1
            g["uids"].add(r.student_id)
            g["solve"] += r.solve_time_ms or 0
            g["path"] += r.path_length or 0
            g["pause"] += r.pause_count or 0
            if r.interaction_result == "correct":
                g["correct"] += 1
        records = []
        for (gb, gd, src, inp), g in groups.items():
            if len(g["uids"]) < K_ANON_MIN:  # k-익명성: 소집단 제외
                k_dropped += 1
                continue
            n = g["n"]
            records.append({
                "grade_band": gb, "gender": gd, "source_type": src, "input_type": inp,
                "n_events": n, "n_students": len(g["uids"]),
                "avg_solve_time_ms": round(g["solve"] / n, 1),
                "avg_path_length": round(g["path"] / n, 1),
                "avg_pause_count": round(g["pause"] / n, 2),
                "correct_rate": round(g["correct"] / n * 100, 1),
            })

    # 내보내기 감사 — 누가·언제·무슨 모드/필터로·몇 건 (재식별 금지 계약 이행 추적)
    audit(
        db, action="behavior.export", actor_user_id=principal.id,
        after={"mode": mode, "fmt": fmt, "dataset": dataset, "source_type": source_type,
               "count": len(records), "k_dropped": k_dropped},
    )
    db.commit()

    stamp = datetime.now().strftime("%Y%m%d")  # KST 날짜 — utcnow면 한국 자정~09시에 전날로 찍힘
    fname = f"catchap_behavior_{mode}_{stamp}.{fmt}"
    if fmt == "json":
        return {
            "mode": mode, "count": len(records), "k_anon_min": K_ANON_MIN,
            "k_dropped": k_dropped, "columns": cols, "rows": records,
        }
    # CSV 수식 인젝션 방어: 문자열 셀이 =+-@ 등으로 시작하면 스프레드시트가 수식으로 실행할 수 있어
    # 앞에 작은따옴표를 붙여 무력화한다(현재 값은 열거형이라 안전하지만 미래 필드까지 대비).
    _danger = ("=", "+", "-", "@", "\t", "\r")

    def _safe(v):
        if isinstance(v, str) and v and v[0] in _danger:
            return "'" + v
        return v

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for rec in records:
        w.writerow({k: _safe(v) for k, v in rec.items()})
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


class _DatasetMarkReq(BaseModel):
    dataset_status: str = Field(pattern="^(candidate|included|excluded)$")


@router.patch("/behavior/records/{record_id}/dataset")
def behavior_mark_dataset(
    record_id: str,
    req: _DatasetMarkReq,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """레코드의 학습셋 상태 변경 (candidate|included|excluded) — 감사 로그 기록."""
    r = db.get(BehaviorSummary, record_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="레코드를 찾을 수 없습니다.")
    before = r.dataset_status
    if before == req.dataset_status:  # no-op은 감사 로그를 남기지 않음 (연타/재호출 노이즈 방지)
        return {"ok": True, "dataset_status": before}
    r.dataset_status = req.dataset_status
    db.add(
        AuditLog(
            actor_user_id=principal.id,
            organization_id=r.organization_id,
            action="behavior.dataset_mark",
            target_type="behavior_summary",
            target_id=r.id,
            after_json={"from": before, "to": req.dataset_status},
        )
    )
    db.commit()
    return {"ok": True, "dataset_status": r.dataset_status}


class _SampleLabelReq(BaseModel):
    sample_label: str = Field(pattern="^(organic|bot|human)$")
    ids: list[str] = Field(min_length=1, max_length=500)


@router.patch("/behavior/records/label")
def behavior_mark_label(
    req: _SampleLabelReq,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """레코드의 지도학습 라벨(sample_label) 일괄 변경 — bot/human 정답표 수동 큐레이션.

    organic(미검증)→human/bot 검토 확정용. 다중 선택 일괄 처리(최대 500건),
    변경된 행만 감사 로그 1건으로 묶어 남긴다(행별 로그는 500건 노이즈).

    'bot' 라벨은 확정이며 되돌릴 수 없다 — 레드팀 주입·자동화 확정분이 사람 라벨로
    뒤집히면 지도학습 정답표가 오염된다. bot 행은 어떤 값으로도 재라벨을 거부하고
    응답의 locked로 건수를 알려준다(콘솔이 안내).
    """
    rows = db.query(BehaviorSummary).filter(BehaviorSummary.id.in_(req.ids)).all()
    locked = [r for r in rows if r.sample_label == "bot"]
    changed = [r for r in rows if r.sample_label != req.sample_label and r.sample_label != "bot"]
    for r in changed:
        r.sample_label = req.sample_label
    if changed:
        db.add(
            AuditLog(
                actor_user_id=principal.id,
                organization_id=None,  # 여러 기관에 걸칠 수 있음
                action="behavior.label_mark",
                target_type="behavior_summary",
                target_id=None,
                after_json={"to": req.sample_label, "count": len(changed),
                            "locked": len(locked),
                            "ids": [r.id for r in changed][:50]},  # 근거 표본
            )
        )
        db.commit()
    return {
        "ok": True,
        "requested": len(req.ids),
        "changed": len(changed),
        "locked": len(locked),  # bot 확정이라 변경 거부된 행 수
    }


class _RedteamReq(BaseModel):
    count: int = Field(default=50, ge=1, le=500)
    seed: int | None = None


@router.post("/behavior/redteam")
def behavior_redteam_generate(
    req: _RedteamReq,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """레드팀 합성 봇 트래픽 생성 — 지도학습 음성 클래스(sample_label='bot') 확보.

    격리된 sentinel org에 적재되어 고객 집계엔 안 잡히고 학습셋 콘솔에서만 보인다.
    """
    from app.services.redteam import inject_bot_behaviors

    created = inject_bot_behaviors(db, req.count, seed=req.seed)
    db.add(
        AuditLog(
            actor_user_id=principal.id,
            organization_id=None,
            action="behavior.redteam_generate",
            target_type="behavior_summary",
            target_id=None,
            after_json={"count": created, "label": "bot"},
        )
    )
    db.commit()
    return {"ok": True, "created": created}
