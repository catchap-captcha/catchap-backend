"""운영자(ops) API — seed 기반 최소 응답 + 기관 가입 승인."""

from datetime import datetime, timedelta, timezone
from html import escape

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.permissions import Principal, require_ops
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
    """그룹(아동/익명)별 행동 지표 평균 — 아동·성인 행동이 갈라지는지 보는 비교 데이터."""
    row = (
        db.query(
            func.count(BehaviorSummary.id),
            func.avg(BehaviorSummary.solve_time_ms),
            func.avg(BehaviorSummary.path_length),
            func.avg(BehaviorSummary.avg_speed),
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
    trace_points = (
        dict(
            db.query(BehaviorTrace.behavior_id, BehaviorTrace.point_count)
            .filter(BehaviorTrace.behavior_id.in_(ids))
            .all()
        )
        if ids
        else {}
    )

    def _row(r: BehaviorSummary) -> dict:
        s = students.get(r.student_id) if r.student_id else None
        org = orgs.get(r.organization_id)
        return {
            "id": r.id,
            "source_type": r.source_type,
            "organization_name": org.name if org else None,
            "student": {
                "nickname": s.nickname,
                "student_code": s.student_code,
                "age": s.age,
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
            "dataset_status": r.dataset_status,
            "trace_points": trace_points.get(r.id),  # None = 원시 궤적 없음
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
