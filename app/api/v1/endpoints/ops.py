"""운영자(ops) API — seed 기반 최소 응답 + 기관 가입 승인."""

from datetime import datetime, timezone
from html import escape

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.permissions import Principal, require_ops
from app.db.session import get_db
from app.email.smtp import send_email
from app.models import (
    ApiKey,
    AuditLog,
    Inquiry,
    InquiryReply,
    Membership,
    ModelVersion,
    Organization,
    OrgRegistrationRequest,
    StudentProfile,
    User,
)

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
    return [
        {
            "id": o.id,
            "name": o.name,
            "code": o.code,
            "org_type": o.org_type,
            "status": o.status,
            "students": db.query(StudentProfile)
            .filter(StudentProfile.organization_id == o.id, StudentProfile.status != "disabled")
            .count(),
        }
        for o in rows
    ]


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
    return [
        {
            "id": log.id,
            "action": log.action,
            "actor_user_id": log.actor_user_id,
            "organization_id": log.organization_id,
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
