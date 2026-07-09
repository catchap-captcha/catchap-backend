"""운영자(ops) API — seed 기반 최소 응답 + 기관 가입 승인."""

import csv
import hashlib
import io
import secrets
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from html import escape

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.utils.helpers import audit

from app.core.config import get_settings
from app.core.permissions import Principal, require_ops
from app.core.security import hash_password
from app.db.session import get_db
from app.email.smtp import send_email
from app.models import (
    ApiKey,
    AuditLog,
    BehaviorSummary,
    BehaviorTrace,
    Inquiry,
    InquiryReply,
    Membership,
    ModelVersion,
    Organization,
    OrgRegistrationRequest,
    Plan,
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
        "api_calls_today": 3912,
        "error_rate": "0.3%",
    }


@router.get("/orgs")
def orgs(principal: Principal = Depends(require_ops), db: Session = Depends(get_db)):
    rows = db.query(Organization).order_by(Organization.created_at).all()
    return [_org_admin_row(db, o) for o in rows]


# ---------------------------------------------------------------- 기관 등록/수정/삭제 (운영자)
# 운영자 콘솔은 기관 '엔티티'만 관리한다. 학생 명단·실명 등 기관 내부 데이터는
# 여기서 다루지 않으며(아동 PII 분리), 그건 기관 관리자/학년부장의 /orgs/* 콘솔 몫이다.
def _org_admin_row(db: Session, o: Organization) -> dict:
    """운영자 기관 목록/상세 행 — 기관 메타 + 학생 수(집계값만, PII 아님)."""
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
        "students": db.query(StudentProfile)
        .filter(StudentProfile.organization_id == o.id, StudentProfile.status != "disabled")
        .count(),
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
        email_verified_at=_now(),
    )
    db.add(admin)
    db.flush()
    db.add(
        Membership(
            user_id=admin.id,
            organization_id=org.id,
            role="org_admin",
            status="active",
            joined_at=_now(),
        )
    )
    # 기본 요금제(basic) 연결 — 키 발급·요금제 게이팅이 동작하도록
    basic = db.query(Plan).filter(Plan.key == "basic").first()
    if basic:
        db.add(Subscription(organization_id=org.id, plan_id=basic.id))
    db.add(
        AuditLog(
            actor_user_id=principal.id,
            organization_id=org.id,
            action="org.create",
            target_type="organization",
            target_id=org.id,
            after_json={"name": org.name, "code": org.code, "admin_email": admin_email},
        )
    )
    db.commit()
    return {
        "ok": True,
        **_org_admin_row(db, org),
        "admin_email": admin_email,
        "admin_temp_password": temp_password,  # 1회 노출 — 응답 이후 조회 불가
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
    # 빈 기관: 관리자 계정·멤버십·구독·가입신청·기관을 정리한다.
    db.query(Membership).filter(Membership.organization_id == org_id).delete()
    db.query(Subscription).filter(Subscription.organization_id == org_id).delete()
    db.query(User).filter(User.organization_id == org_id).delete()
    db.query(OrgRegistrationRequest).filter(
        OrgRegistrationRequest.organization_id == org_id
    ).delete()
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
    return {"ok": True}


@router.get("/inquiries")
def inquiries(
    status_filter: str | None = None,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """문의하기 접수 목록 (status_filter: received|resolved, 없으면 전체). 최신순.

    각 문의에 운영자 답변 스레드(replies)를 시간순으로 함께 반환한다.
    """
    q = db.query(Inquiry).order_by(Inquiry.created_at.desc())
    if status_filter:
        q = q.filter(Inquiry.status == status_filter)
    items = q.all()

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

    return [
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
    ]


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
def system(principal: Principal = Depends(require_ops)):
    return {
        "services": [
            {"name": "api", "status": "ok", "latency_ms": 42},
            {"name": "db", "status": "ok", "latency_ms": 6},
            {"name": "captcha-engine", "status": "stub", "latency_ms": 0},
            {"name": "smtp", "status": "dry-run", "latency_ms": 0},
        ]
    }


@router.get("/logs")
def logs(principal: Principal = Depends(require_ops), db: Session = Depends(get_db)):
    rows = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(50).all()
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

    return [
        {
            "id": log.id,
            "action": log.action,
            "actor_user_id": log.actor_user_id,
            "actor_name": _actor(log),
            "organization_id": log.organization_id,
            "org_name": orgs[log.organization_id].name if log.organization_id in orgs else None,
            "target_type": log.target_type,
            "target_id": log.target_id,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in rows
    ]


@router.get("/ai-models")
def ai_models(principal: Principal = Depends(require_ops), db: Session = Depends(get_db)):
    rows = db.query(ModelVersion).order_by(ModelVersion.created_at).all()
    return [
        {
            "id": m.id,
            "category": m.category,
            "name": m.name,
            "provider": m.provider,
            "version": m.version,
            "status": m.status,
        }
        for m in rows
    ]


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
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """기관 가입 신청 목록 (status_filter: pending|approved|rejected, 없으면 전체)."""
    q = db.query(OrgRegistrationRequest).order_by(OrgRegistrationRequest.created_at.desc())
    if status_filter:
        q = q.filter(OrgRegistrationRequest.status == status_filter)
    return [_req_row(r, db) for r in q.all()]


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
    r.approved_at = _now()
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
    return {"ok": True, "status": "approved"}


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


def _apikey_row(db: Session, k: ApiKey) -> dict:
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
        "site_key": k.site_key,  # 공개키 — 목록 노출 OK
        "status": k.status,
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
def ops_list_api_keys(principal: Principal = Depends(require_ops), db: Session = Depends(get_db)):
    rows = db.query(ApiKey).filter(ApiKey.status != "deleted").order_by(ApiKey.created_at.desc()).all()
    return [_apikey_row(db, k) for k in rows]


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
    )
    db.add(
        AuditLog(
            actor_user_id=principal.id, organization_id=req.organization_id,
            action="captcha.api_key_issue", target_type="api_key", target_id=issued["id"],
            after_json={"product": req.product, "subject": req.subject, "label": req.label},
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

    return {
        "total": total,
        "week_count": week_count,
        "trace_count": db.query(BehaviorTrace).count(),  # 원시 궤적이 남은 레코드 수
        "by_source": _group_counts(BehaviorSummary.source_type),
        "by_result": _group_counts(BehaviorSummary.interaction_result),
        "by_risk": _group_counts(BehaviorSummary.risk_level),
        "by_dataset": _group_counts(BehaviorSummary.dataset_status),
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
    limit: int = 50,
    offset: int = 0,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """행동 데이터 레코드 목록 (필터 + 페이지네이션, 최신순)."""
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

    stamp = datetime.utcnow().strftime("%Y%m%d")
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
