"""기관 관리자 API — 자기 기관만 (require_org_admin + check_org_scope)."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.permissions import Principal, check_org_scope, require_org_admin
from app.db.session import get_db
from app.models import (
    ApiKey,
    CaptchaSetting,
    ClassRoom,
    Invoice,
    LearningSummary,
    Membership,
    ModelVersion,
    Organization,
    ParentStudentLink,
    PaymentMethod,
    Plan,
    Site,
    StudentProfile,
    Subscription,
    User,
)
import secrets as _secrets

from app.core.security import hash_password as _hash_password
from app.schemas.org import CaptchaSettingsUpdate, OrgUpdate, TeacherCreate, TeacherUpdate
from app.services import aggregate, onboarding_service
from app.services import auth_service as _auth_service
from app.services.aggregate import fb
from pydantic import BaseModel as _BaseModel


class _RegisterStudentsReq(_BaseModel):
    count: int = 1
    class_label: str | None = None
    class_id: str | None = None
from app.services.stats import D  # DB(stat_blobs) 우선, design_data fallback
from app.utils.helpers import audit

router = APIRouter(prefix="/orgs", tags=["orgs"])


def _org(db: Session, org_id: str) -> Organization:
    org = db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="기관을 찾을 수 없습니다.")
    return org


def _org_row(db: Session, org: Organization) -> dict:
    # 대표 관리자: 실테이블(users, role=org_admin) 첫 번째
    admin = (
        db.query(User)
        .filter(User.organization_id == org.id, User.role == "org_admin", User.status != "disabled")
        .order_by(User.created_at)
        .first()
    )
    return {
        "id": org.id,
        "name": org.name,
        "code": org.code,
        "org_type": org.org_type,
        "status": org.status,
        "contact_email": org.contact_email,
        "contact_phone": org.contact_phone,
        "address": org.address,
        "business_number": org.business_number,
        "admin": admin.name if admin else None,
        "tax_email": D.ORG_TAX_EMAIL,  # organizations 컬럼 없음 — stat_blobs(D)
        "code_expires_at": org.code_expires_at.isoformat() if org.code_expires_at else None,
        "code_remain_days": (
            max(0, (org.code_expires_at - datetime.utcnow()).days) if org.code_expires_at else None
        ),
    }


@router.get("/me")
def my_org(principal: Principal = Depends(require_org_admin), db: Session = Depends(get_db)):
    if not principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="소속 기관이 없습니다.")
    return _org_row(db, _org(db, principal.organization_id))


@router.patch("/{org_id}")
def update_org(
    org_id: str,
    req: OrgUpdate,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    check_org_scope(principal, org_id)
    org = _org(db, org_id)
    before = {
        "name": org.name,
        "org_type": org.org_type,
        "contact_email": org.contact_email,
        "contact_phone": org.contact_phone,
        "address": org.address,
        "business_number": org.business_number,
    }
    for field in (
        "name",
        "org_type",
        "contact_email",
        "contact_phone",
        "address",
        "business_number",
    ):
        value = getattr(req, field)
        if value is not None:
            setattr(org, field, value)
    audit(
        db,
        action="org.update",
        actor_user_id=principal.id,
        organization_id=org_id,
        target_type="organization",
        target_id=org_id,
        before=before,
        after={k: getattr(org, k) for k in before},
    )
    db.commit()
    return _org_row(db, org)


# ---------------------------------------------------------------- 대시보드/분석
@router.get("/{org_id}/dashboard")
def dashboard(
    org_id: str,
    period: str = Query(default="week"),
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    check_org_scope(principal, org_id)
    p = period if period in D.ORG_DASHBOARD else "week"
    # 실집계 덮어쓰기: kStudents/kApi/kPass/kFail/kAvg/dLow·dReview·dElevated/grades
    # (api_usage_logs·behavior_summaries·learning_attempts — 원천 없으면 D 유지.
    #  봇차단 시계열(block/pass) 등 원천 없는 항목은 D 그대로.)
    overrides = aggregate.org_dashboard_overrides(db, org_id, p)
    # 학급별 요약 표 — learning_attempts 학급 group 실집계 (없으면 D)
    start, end = aggregate._org_period_range(p)
    extras = aggregate.org_analytics_extras(db, org_id, start, end)
    return {
        "period": p,
        **D.ORG_DASHBOARD[p],
        "grades": D.ORG_DASHBOARD_GRADES,
        "gradeBars": D.ORG_DASHBOARD_BARS,
        "classes": fb(extras.get("classes"), D.ORG_ANALYTICS_CLASSES),
        **overrides,
        "site": _site_status_payload(db, org_id),
    }


@router.get("/{org_id}/analytics")
def analytics(
    org_id: str,
    period: str = Query(default="week"),
    subject: str | None = Query(default=None),
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    check_org_scope(principal, org_id)
    p = period if period in D.ORG_ANALYTICS else "week"
    d = dict(D.ORG_ANALYTICS[p])
    acc = list(d["accPct"])
    ts = subject if subject in D.ORG_ANALYTICS_SUBJ_LAST else None
    if ts:
        shift = D.ORG_ANALYTICS_SUBJ_LAST[ts] - acc[-1]
        acc = [max(45, min(99, v + shift)) for v in acc]
    d_subjects = [
        {**s, "correct": round(s["total"] * s["pct"] / 100), "meta": D.SUBJECT_META.get(s["name"], {})}
        for s in D.ORG_ANALYTICS_SUBJECTS
    ]

    # 전교 실집계 (teacher analytics의 기관 버전) — 시도 없으면 D 유지
    students = (
        db.query(StudentProfile)
        .filter(StudentProfile.organization_id == org_id, StudentProfile.status != "disabled")
        .all()
    )
    agg = aggregate.analytics(db, students, p, len(d["axis"]), ts) or {}
    if agg.get("subjects"):
        agg["subjects"] = [
            {**s, "meta": D.SUBJECT_META.get(s["name"], {})} for s in agg["subjects"]
        ]
    buckets, start, end = aggregate._period_buckets(p, len(d["axis"]))
    extras = aggregate.org_analytics_extras(db, org_id, start, end)

    return {
        "period": p,
        "subject": ts or "all",
        **{k: v for k, v in d.items() if k != "accPct"},
        **{k: agg[k] for k in ("kAcc", "kAccDelta", "kActive", "kSolved", "kHelp") if k in agg},
        "accSeries": fb(agg.get("accSeries"), acc),
        "avg": fb(agg.get("avg"), round(sum(acc) / len(acc))),
        "subjects": fb(agg.get("subjects"), d_subjects),
        "grades": fb(extras.get("grades"), D.ORG_ANALYTICS_GRADES),
        "classes": fb(extras.get("classes"), D.ORG_ANALYTICS_CLASSES),
        "reasons": fb(agg.get("reasons"), D.ORG_ANALYTICS_REASONS),
        "subjTarget": "85%",
        "gradeTarget": "85%",
        "ai_summary": D.ORG_ANALYTICS_AI,  # AI 분석 요약 (stat_blobs 수정 가능)
    }


# ---------------------------------------------------------------- 학급/roster
@router.get("/{org_id}/classes")
def classes(
    org_id: str,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    check_org_scope(principal, org_id)
    rows = (
        db.query(ClassRoom)
        .filter(ClassRoom.organization_id == org_id, ClassRoom.status == "active")
        .order_by(ClassRoom.name)
        .all()
    )
    design = {c["name"]: c for c in D.ORG_CLASSES}
    # 학급별 실제 학생 수 (student_profiles.class_id 기준)
    counts = dict(
        db.query(StudentProfile.class_id, func.count(StudentProfile.id))
        .filter(
            StudentProfile.organization_id == org_id,
            StudentProfile.class_id.isnot(None),
            StudentProfile.status != "disabled",
        )
        .group_by(StudentProfile.class_id)
        .all()
    )
    out = []
    for c in rows:
        d = design.get(c.name, {})
        teacher_user = db.get(User, c.teacher_id) if c.teacher_id else None
        real_count = int(counts.get(c.id, 0))
        out.append(
            {
                "id": c.id,
                "key": d.get("key", c.name.replace("반", "")),
                "name": c.name,
                "grade": c.grade,
                # 담당 교사: 실테이블(classes.teacher_id → users) 우선
                "teacher": teacher_user.name if teacher_user else d.get("teacher", "미배정"),
                # 학생 수: 실테이블 우선 (배정 학생이 없으면 디자인 수치 유지)
                "count": real_count or d.get("count", 0),
                "acc": d.get("acc", 0),
                "risk": d.get("risk", "낮음"),
            }
        )
    return out


def _acc_pct(summary) -> int:
    if summary is None or not summary.total_count:
        return 0
    return round(summary.correct_count / summary.total_count * 100)


def _roster_display_name(s: StudentProfile) -> str:
    """닉네임이 디자인 매핑과 일치할 때만 '성 포함 표기', 아니면 DB 닉네임."""
    full = D.CODE_FULL_NAME.get(s.student_code)
    if full and s.nickname and s.nickname in full:
        return full
    return s.nickname


@router.get("/{org_id}/roster")
def roster(
    org_id: str,
    cls: str | None = Query(default=None),
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    check_org_scope(principal, org_id)
    # 학급 배정된 기관 학생 전체 — 실테이블(student_profiles/classes) 기준
    students = (
        db.query(StudentProfile)
        .filter(
            StudentProfile.organization_id == org_id,
            StudentProfile.class_id.isnot(None),
            StudentProfile.status != "disabled",
        )
        .order_by(StudentProfile.student_code)
        .all()
    )
    class_names = {
        c.id: c.name
        for c in db.query(ClassRoom).filter(ClassRoom.organization_id == org_id).all()
    }
    summaries = {
        r.student_id: r
        for r in db.query(LearningSummary)
        .filter(
            LearningSummary.organization_id == org_id,
            LearningSummary.period_type == "week",
        )
        .all()
    }
    linked_ids = {
        l.student_id
        for l in db.query(ParentStudentLink)
        .filter(
            ParentStudentLink.organization_id == org_id,
            ParentStudentLink.status == "approved",
        )
        .all()
    }
    out = []
    for s in students:
        meta = D.ORG_ROSTER_META.get(s.student_code, {})
        cls_name = class_names.get(s.class_id) or meta.get("cls")
        if cls and cls_name != cls:
            continue
        acc = _acc_pct(summaries.get(s.id)) or meta.get("acc", 0)
        out.append(
            {
                "id": s.id,
                "name": _roster_display_name(s),
                "age": s.age,
                "cls": cls_name,
                "code": s.student_code,
                "link": s.id in linked_ids or bool(meta.get("link")),
                "acc": acc,
                "risk": meta.get("risk") or ("주의" if acc < 75 else "낮음"),
            }
        )
    total = (
        db.query(StudentProfile)
        .filter(StudentProfile.organization_id == org_id, StudentProfile.status != "disabled")
        .count()
    )
    org = _org(db, org_id)
    # 헤더 요약용 실카운트 (classes/memberships 실테이블)
    class_count = (
        db.query(ClassRoom)
        .filter(ClassRoom.organization_id == org_id, ClassRoom.status == "active")
        .count()
    )
    teacher_count = (
        db.query(Membership)
        .filter(
            Membership.organization_id == org_id,
            Membership.role == "teacher",
            Membership.status != "disabled",
        )
        .count()
    )
    return {
        "total": total,
        "shown": len(out),
        "students": out,
        "org_join_code": org.code,
        "class_count": class_count,
        "teacher_count": teacher_count,
    }


# ---------------------------------------------------------------- 선생님 관리
def _teacher_row(db: Session, m: Membership) -> dict:
    user = db.get(User, m.user_id) if m.user_id else None
    cls = (
        db.query(ClassRoom)
        .filter(ClassRoom.teacher_id == m.user_id, ClassRoom.status == "active")
        .order_by(ClassRoom.name)
        .first()
        if m.user_id
        else None
    )
    design = next((t for t in D.ORG_TEACHERS if user and t["name"] == user.name), None)
    return {
        "id": m.id,
        "user_id": m.user_id,
        "name": user.name if user else "미등록",
        "email": user.email if user else None,
        "cls": cls.name if cls else (design["cls"] if design else None),
        "role": m.position or "담임",
        "code": m.teacher_code,
        "years": m.career_years or 0,
        "status": "active" if m.status == "active" else "pending",
    }


@router.get("/{org_id}/teachers")
def teachers(
    org_id: str,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    check_org_scope(principal, org_id)
    rows = (
        db.query(Membership)
        .filter(
            Membership.organization_id == org_id,
            Membership.role == "teacher",
            Membership.status != "disabled",
        )
        .order_by(Membership.created_at)
        .all()
    )
    return [_teacher_row(db, m) for m in rows]


@router.post("/{org_id}/teachers")
def add_teacher(
    org_id: str,
    req: TeacherCreate,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    check_org_scope(principal, org_id)
    code = req.teacher_code.strip().upper()
    if db.query(Membership).filter(Membership.teacher_code == code).first():
        raise HTTPException(status.HTTP_409_CONFLICT, detail="이미 사용 중인 교사 코드입니다.")
    # 코드 선발급 → 가입 시 클레임: Membership user_id=null 로 생성 (스펙)
    membership = Membership(
        user_id=None,
        organization_id=org_id,
        role="teacher",
        status="pending",
        teacher_code=code,
        position=req.role,
        invited_by=principal.id,
    )
    db.add(membership)
    db.flush()
    # 표시용 이름/이메일은 pending User 자리로 보관 (가입 시 클레임)
    if req.email:
        from app.core.security import generate_token, hash_password

        existing_user = db.query(User).filter(User.email == req.email).first()
        if existing_user is None:
            placeholder = User(
                email=req.email,
                password_hash=hash_password(generate_token()[:32]),
                name=req.name,
                role="teacher",
                status="pending",
                organization_id=org_id,
            )
            db.add(placeholder)
            db.flush()
            membership.user_id = placeholder.id
    audit(
        db,
        action="org.teacher_add",
        actor_user_id=principal.id,
        organization_id=org_id,
        target_type="membership",
        target_id=membership.id,
        after={"name": req.name, "teacher_code": code, "role": req.role, "class": req.class_name},
    )
    db.commit()
    return {"ok": True, "teacher": _teacher_row(db, membership)}


@router.patch("/{org_id}/teachers/{teacher_id}")
def update_teacher(
    org_id: str,
    teacher_id: str,
    req: TeacherUpdate,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    check_org_scope(principal, org_id)
    m = db.get(Membership, teacher_id)
    if m is None or m.organization_id != org_id or m.role != "teacher":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="선생님을 찾을 수 없습니다.")
    before = _teacher_row(db, m)
    if req.role is not None:
        m.position = req.role
    user = db.get(User, m.user_id) if m.user_id else None
    if user:
        if req.name is not None and req.name.strip():
            user.name = req.name.strip()
        if req.email is not None:
            new_email = req.email.strip().lower()
            if new_email != user.email:
                taken = (
                    db.query(User)
                    .filter(User.email == new_email, User.id != user.id)
                    .first()
                )
                if taken:
                    raise HTTPException(
                        status.HTTP_409_CONFLICT, detail="이미 사용 중인 이메일입니다."
                    )
                user.email = new_email
    audit(
        db,
        action="org.teacher_update",
        actor_user_id=principal.id,
        organization_id=org_id,
        target_type="membership",
        target_id=m.id,
        before={"name": before["name"], "role": before["role"], "email": before["email"]},
        after={"name": req.name, "role": req.role, "email": req.email},
    )
    db.commit()
    return {"ok": True, "teacher": _teacher_row(db, m)}


@router.delete("/{org_id}/teachers/{teacher_id}")
def delete_teacher(
    org_id: str,
    teacher_id: str,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    check_org_scope(principal, org_id)
    m = db.get(Membership, teacher_id)
    if m is None or m.organization_id != org_id or m.role != "teacher":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="선생님을 찾을 수 없습니다.")
    # 삭제 규칙: 담임 + 담당 학급 학생 > 0 이면 409
    if (m.position or "담임") == "담임" and m.user_id:
        cls = (
            db.query(ClassRoom)
            .filter(ClassRoom.teacher_id == m.user_id, ClassRoom.status == "active")
            .first()
        )
        if cls:
            count = (
                db.query(StudentProfile)
                .filter(StudentProfile.class_id == cls.id, StudentProfile.status != "disabled")
                .count()
            )
            design = next((c for c in D.ORG_CLASSES if c["name"] == cls.name), None)
            display_count = count or (design["count"] if design else 0)
            if display_count > 0:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail={
                        "message": f"{cls.name}에 학생 {display_count}명이 배정되어 있어 삭제할 수 없습니다. 먼저 담임을 변경해 주세요.",
                        "count": display_count,  # 프론트 모달 실카운트
                        "cls": cls.name,
                    },
                )
    row = _teacher_row(db, m)
    m.status = "disabled"
    audit(
        db,
        action="org.teacher_delete",
        actor_user_id=principal.id,
        organization_id=org_id,
        target_type="membership",
        target_id=m.id,
        before=row,
    )
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- 캡차 설정
@router.get("/{org_id}/captcha-settings")
def captcha_settings(
    org_id: str,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    check_org_scope(principal, org_id)
    row = db.query(CaptchaSetting).filter(CaptchaSetting.organization_id == org_id).first()
    if row is None:
        return {
            "active_types": {"image_select": True, "word_select": True, "drag": False, "arithmetic": False},
            "round_count": 2,
            "shuffle": True,
        }
    return {
        "active_types": row.active_types or {},
        "round_count": row.round_count,
        "shuffle": row.shuffle,
    }


@router.put("/{org_id}/captcha-settings")
def save_captcha_settings(
    org_id: str,
    req: CaptchaSettingsUpdate,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    check_org_scope(principal, org_id)
    row = db.query(CaptchaSetting).filter(CaptchaSetting.organization_id == org_id).first()
    before = None
    if row is None:
        row = CaptchaSetting(organization_id=org_id)
        db.add(row)
    else:
        before = {
            "active_types": row.active_types,
            "round_count": row.round_count,
            "shuffle": row.shuffle,
        }
    row.active_types = req.active_types
    row.round_count = req.round_count
    row.shuffle = req.shuffle
    audit(
        db,
        action="org.captcha_settings_update",
        actor_user_id=principal.id,
        organization_id=org_id,
        target_type="captcha_setting",
        target_id=row.id,
        before=before,
        after={"active_types": req.active_types, "round_count": req.round_count, "shuffle": req.shuffle},
    )
    db.commit()
    return {"ok": True, "active_types": row.active_types, "round_count": row.round_count, "shuffle": row.shuffle}


# ---------------------------------------------------------------- AI 모델
@router.get("/{org_id}/ai-models")
def ai_models(
    org_id: str,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    check_org_scope(principal, org_id)
    rows = db.query(ModelVersion).order_by(ModelVersion.created_at).all()
    return {
        "registry_version": D.MODEL_REGISTRY_VERSION,  # stat_blobs(D) 수정 가능
        "models": [
            {
                "id": m.id,
                "cat": m.category,
                "name": m.name,
                "provider": m.provider,
                "version": m.version,
                "status": m.status,
                "use": m.description,
                "updated": m.updated_on,
            }
            for m in rows
        ],
        "changelog": D.MODEL_CHANGELOG,
    }


# ---------------------------------------------------------------- 요금제/관리자
@router.get("/{org_id}/billing")
def billing(
    org_id: str,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    check_org_scope(principal, org_id)
    plans = db.query(Plan).order_by(Plan.order_no).all()
    sub = db.query(Subscription).filter(Subscription.organization_id == org_id).first()
    current_plan = db.get(Plan, sub.plan_id) if sub else None
    students = (
        db.query(StudentProfile)
        .filter(StudentProfile.organization_id == org_id, StudentProfile.status != "disabled")
        .count()
    )
    cards = (
        db.query(PaymentMethod)
        .filter(PaymentMethod.organization_id == org_id)
        .order_by(PaymentMethod.is_default.desc())
        .all()
    )
    invoices = (
        db.query(Invoice)
        .filter(Invoice.organization_id == org_id)
        .order_by(Invoice.billed_on.desc())
        .all()
    )
    usage = D.BILLING_USAGE
    teachers_used = (
        db.query(Membership)
        .filter(
            Membership.organization_id == org_id,
            Membership.role == "teacher",
            Membership.status == "active",
        )
        .count()
    )
    return {
        "plans": [
            {
                "id": p.id,
                "key": p.key,
                "name": p.name,
                "monthly_price": p.monthly_price,
                "yearly_price": p.yearly_price,
                "student_seats": p.student_seats,
                "teacher_seats": p.teacher_seats,
                "api_quota": p.api_quota,
                "features": p.features,
            }
            for p in plans
        ],
        "subscription": {
            "plan_key": current_plan.key if current_plan else None,
            "plan_name": current_plan.name if current_plan else None,
            "billing_cycle": sub.billing_cycle if sub else "monthly",
            "status": sub.status if sub else None,
            "auto_renew": sub.auto_renew if sub else True,
            "next_billing_date": usage["next_billing_date"],
        },
        "usage": {
            # API 사용량: api_usage_logs 이번 달 실카운트 (없으면 D)
            "api": {
                **usage["api"],
                "used": aggregate.org_api_usage_month(db, org_id) or usage["api"]["used"],
                "quota": current_plan.api_quota if current_plan and current_plan.api_quota else usage["api"]["quota"],
            },
            "student_seats": {
                "used": students,  # 실테이블(student_profiles) 기준
                "registered": students,
                "quota": current_plan.student_seats if current_plan else 300,
            },
            "teacher_seats": {
                "used": teachers_used or usage["teacher_seats"]["used"],
                "quota": (
                    current_plan.teacher_seats
                    if current_plan and current_plan.teacher_seats
                    else usage["teacher_seats"]["quota"]
                ),
            },
        },
        "payment_methods": [
            {
                "id": c.id,
                "card_brand": c.card_brand,
                "card_last4": c.card_last4,
                "is_default": c.is_default,
                # 만료일: payment_methods 컬럼 없음 — last4 기준 stat_blobs(D)
                "exp": D.BILLING_CARD_EXP.get(c.card_last4),
            }
            for c in cards
        ],
        "invoices": [
            {
                "id": v.id,
                "invoice_no": v.invoice_no,
                "date": v.billed_on,
                "item": v.description,
                "amount": v.amount,
                "status": v.status,
            }
            for v in invoices
        ],
    }


@router.get("/{org_id}/admins")
def admins(
    org_id: str,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    check_org_scope(principal, org_id)
    users = (
        db.query(User)
        .filter(User.organization_id == org_id, User.role == "org_admin", User.status != "disabled")
        .order_by(User.created_at)
        .all()
    )
    role_map = {a["email"]: a["role"] for a in D.ORG_ADMINS}
    return [
        {
            "id": u.id,
            "name": u.name,
            "email": u.email,
            "role": role_map.get(u.email, "최고 관리자" if i == 0 else "조회 전용"),
        }
        for i, u in enumerate(users)
    ]


# ---------------------------------------------------------------- API·사이트 상태
def _site_status_payload(db: Session, org_id: str) -> dict:
    site = db.query(Site).filter(Site.organization_id == org_id).first()
    key = (
        db.query(ApiKey)
        .filter(ApiKey.organization_id == org_id, ApiKey.status == "active")
        .first()
    )
    masked = None
    if key:
        sk = key.site_key
        masked = f"{sk[:11]}•••{sk[-2:]}" if len(sk) > 13 else sk
    # 호출 수/에러율/지연: api_usage_logs 실집계 (없으면 디자인 수치 유지)
    from datetime import date as _date

    from app.models import ApiUsageLog

    today_n = (
        db.query(func.count(ApiUsageLog.id))
        .filter(
            ApiUsageLog.organization_id == org_id,
            ApiUsageLog.created_at >= datetime.combine(_date.today(), datetime.min.time()),
        )
        .scalar()
        or 0
    )
    month_first = _date.today().replace(day=1)
    month_n = (
        db.query(func.count(ApiUsageLog.id))
        .filter(
            ApiUsageLog.organization_id == org_id,
            ApiUsageLog.created_at >= datetime.combine(month_first, datetime.min.time()),
        )
        .scalar()
        or 0
    )
    error_rate, avg_latency = None, None
    if month_n:
        err_n = (
            db.query(func.count(ApiUsageLog.id))
            .filter(
                ApiUsageLog.organization_id == org_id,
                ApiUsageLog.created_at >= datetime.combine(month_first, datetime.min.time()),
                ApiUsageLog.status_code >= 500,
            )
            .scalar()
            or 0
        )
        error_rate = f"{err_n / month_n * 100:.1f}%"
        avg_latency = round(
            db.query(func.avg(ApiUsageLog.latency_ms))
            .filter(
                ApiUsageLog.organization_id == org_id,
                ApiUsageLog.created_at >= datetime.combine(month_first, datetime.min.time()),
            )
            .scalar()
            or 0
        )
    return {
        "status": "정상",
        "message": "모든 서비스 정상 작동 중",
        "site_key": masked,
        "domain": site.domain if site else None,
        # 이번 달 로그가 하나라도 있으면 실집계 (오늘 0건도 실데이터), 없으면 D 유지
        "calls_today": today_n if month_n else 3912,
        "calls_month": month_n if month_n else 86540,
        "error_rate": fb(error_rate, "0.3%"),
        "avg_latency_ms": fb(avg_latency, 142),
    }


@router.get("/{org_id}/site-status")
def site_status(
    org_id: str,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    check_org_scope(principal, org_id)
    return _site_status_payload(db, org_id)


# ---------------------------------------------------------------- 사이드바 위젯
@router.get("/{org_id}/sidebar")
def sidebar(
    org_id: str,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """OrgLayout 사이드바 위젯 — pro(API 사용률)/semester(담임 배정)/insight 실집계, 없으면 D."""
    check_org_scope(principal, org_id)
    d = D.ORG_SIDEBAR

    # pro: 이번 달 API 사용률 (api_usage_logs / plan quota)
    pro = dict(d.get("pro", {}))
    sub_row = db.query(Subscription).filter(Subscription.organization_id == org_id).first()
    plan = db.get(Plan, sub_row.plan_id) if sub_row else None
    used = aggregate.org_api_usage_month(db, org_id)
    quota = (plan.api_quota if plan and plan.api_quota else None) or D.BILLING_USAGE["api"]["quota"]
    if used and quota:
        pct = min(100, round(used / quota * 100))
        pro = {"pct": pct, "sub": f"이번 달 API {pct}% 사용"}
    pro["plan_name"] = plan.name if plan else "Pro"

    # semester: 담임 배정 완료 학급 수 (classes.teacher_id 실테이블)
    semester = dict(d.get("semester", {}))
    class_rows = (
        db.query(ClassRoom)
        .filter(ClassRoom.organization_id == org_id, ClassRoom.status == "active")
        .all()
    )
    if class_rows:
        total = len(class_rows)
        done = sum(1 for c in class_rows if c.teacher_id)
        semester = {
            "done": done,
            "total": total,
            "pct": round(done / total * 100),
            "sub": str(D.ORG_SIDEBAR_SEMESTER_TPL).replace("{total}", str(total)).replace("{done}", str(done)),
        }

    # insight: 이번 주 vs 지난주 과목별 정답률 delta 최대 과목 (learning_attempts)
    insight = dict(d.get("insight", {}))
    from datetime import date, timedelta

    today = date.today()
    ws = today - timedelta(days=today.weekday())
    rows = aggregate.attempts(db, org_id=org_id, since=ws - timedelta(weeks=1))
    cur = [r for r in rows if r.created_at and r.created_at.date() >= ws]
    prev = [r for r in rows if r.created_at and r.created_at.date() < ws]
    best: tuple | None = None
    if cur and prev:
        for subject in D.SUBJECT_ORDER:
            c = [r for r in cur if r.subject == subject]
            p = [r for r in prev if r.subject == subject]
            if len(c) < 3 or len(p) < 3:
                continue
            acc_c = round(sum(1 for r in c if r.result == "correct") / len(c) * 100)
            acc_p = round(sum(1 for r in p if r.result == "correct") / len(p) * 100)
            delta = acc_c - acc_p
            if best is None or delta > best[1]:
                best = (subject, delta)
    if best:
        subject, delta = best
        insight = {
            "sub": str(D.ORG_SIDEBAR_INSIGHT_TPL)
            .replace("{subject}", subject)
            .replace("{delta}", f"{'+' if delta >= 0 else ''}{delta}")
            .replace("{dir}", "상승" if delta >= 0 else "하락")
        }

    return {"pro": pro, "semester": semester, "insight": insight}


# ---------------------------------------------------------------- 보안/개인정보 통계
@router.get("/{org_id}/security-stats")
def security_stats(
    org_id: str,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """보안 정책 화면 통계 — 보호자 동의(연결) 완료율 실집계 (없으면 D)."""
    check_org_scope(principal, org_id)
    total = (
        db.query(StudentProfile)
        .filter(StudentProfile.organization_id == org_id, StudentProfile.status != "disabled")
        .count()
    )
    linked = (
        db.query(func.count(func.distinct(ParentStudentLink.student_id)))
        .filter(
            ParentStudentLink.organization_id == org_id,
            ParentStudentLink.status == "approved",
        )
        .scalar()
        or 0
    )
    consent_rate = f"{min(100, linked / total * 100):.1f}%" if total and linked else None
    return {"consent_rate": fb(consent_rate, D.ORG_CONSENT_RATE)}


# ---------------------------------------------------------------- 학생 등록 · 가입코드 (온보딩)
@router.post("/{org_id}/students/register")
def register_students(
    org_id: str,
    req: _RegisterStudentsReq,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """학생 슬롯 N개 생성 + 1회용 가입 코드 발급. 코드 원문은 이 응답에서만 노출."""
    check_org_scope(principal, org_id)
    # 타 기관 소속 class_id를 주입해 학생을 남의 학급에 귀속시키는 것을 차단
    if req.class_id:
        cls = db.get(ClassRoom, req.class_id)
        if cls is None or cls.organization_id != org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="학급을 찾을 수 없습니다.")
    codes = onboarding_service.generate_join_codes(
        db,
        organization_id=org_id,
        count=req.count,
        class_label=req.class_label,
        class_id=req.class_id,
        created_by=principal.id,
    )
    return {"ok": True, "issued": codes}


@router.post("/{org_id}/students/{student_id}/invite-code")
def issue_invite(
    org_id: str,
    student_id: str,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """학생 1명 귀속 학부모 초대 코드 발급(고엔트로피·만료·2회 허용)."""
    check_org_scope(principal, org_id)
    _student_in_org(db, org_id, student_id)  # 타 기관 학생 대상 코드 발급 차단(크로스테넌트 IDOR)
    code = onboarding_service.issue_parent_invite(
        db, student_id=student_id, organization_id=org_id, created_by=principal.id
    )
    return {"ok": True, "invite_code": code}


# ---------------------------------------------------------------- 학생 비번 초기화 · 학부모 연결 관리
def _student_in_org(db: Session, org_id: str, student_id: str) -> StudentProfile:
    st = db.get(StudentProfile, student_id)
    if st is None or st.organization_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="학생을 찾을 수 없습니다.")
    return st


@router.post("/{org_id}/students/{student_id}/reset-password")
def reset_student_password(
    org_id: str,
    student_id: str,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """학생 비밀번호 초기화 — 임시 비번 발급 + 기존 세션(refresh) 전부 폐기 + 감사 로그.

    학생은 이메일이 없어 스스로 재설정 불가 → 학교(관리자/교사)가 초기화.
    """
    check_org_scope(principal, org_id)
    st = _student_in_org(db, org_id, student_id)
    temp = f"cat-{_secrets.randbelow(9000) + 1000}"
    st.password_hash = _hash_password(temp)
    st.must_change_password = True  # 첫 로그인 시 새 비번 설정 강제
    _auth_service.logout(db, st.id)  # 기존 refresh 토큰 폐기 → 모든 기기 로그아웃
    audit(
        db,
        action="student.password_reset",
        actor_user_id=principal.id,
        organization_id=org_id,
        target_type="student",
        target_id=st.id,
    )
    db.commit()
    return {"ok": True, "temp_password": temp}  # 임시 비번은 1회 노출


@router.get("/{org_id}/students/{student_id}/parent-links")
def student_parent_links(
    org_id: str,
    student_id: str,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """학생에 연결된 학부모 목록 (누가 연결됐는지 확인 — 유출 대비)."""
    check_org_scope(principal, org_id)
    _student_in_org(db, org_id, student_id)
    links = (
        db.query(ParentStudentLink)
        .filter(ParentStudentLink.student_id == student_id, ParentStudentLink.status == "approved")
        .all()
    )
    out = []
    for lk in links:
        u = db.get(User, lk.parent_user_id)
        out.append(
            {
                "link_id": lk.id,
                "parent_name": u.name if u else None,
                "parent_email": u.email if u else None,
                "linked_at": lk.approved_at.isoformat() if lk.approved_at else None,
            }
        )
    return out


@router.post("/{org_id}/parent-links/{link_id}/revoke")
def revoke_parent_link(
    org_id: str,
    link_id: str,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """학부모 연결 해제 (코드 유출 등) — status=removed + 감사 로그."""
    check_org_scope(principal, org_id)
    lk = db.get(ParentStudentLink, link_id)
    if lk is None or lk.organization_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="연결을 찾을 수 없습니다.")
    lk.status = "removed"
    audit(
        db,
        action="parent_link.revoke",
        actor_user_id=principal.id,
        organization_id=org_id,
        target_type="parent_student_link",
        target_id=lk.id,
    )
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- 학생 반 배정/이동
class _AssignClassReq(_BaseModel):
    class_label: str


@router.patch("/{org_id}/students/{student_id}/class")
def assign_student_class(
    org_id: str,
    student_id: str,
    req: _AssignClassReq,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """학생을 특정 반으로 배정/이동. 반이 없으면 만들어 연결. (반배정)"""
    check_org_scope(principal, org_id)
    st = _student_in_org(db, org_id, student_id)
    label = (req.class_label or "").strip()
    if not label:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="반 이름이 필요합니다.")
    cls = (
        db.query(ClassRoom)
        .filter(ClassRoom.organization_id == org_id, ClassRoom.name == label)
        .first()
    )
    if cls is None:
        cls = ClassRoom(organization_id=org_id, name=label, status="active")
        db.add(cls)
        db.flush()
    before = {"class_id": st.class_id}
    st.class_id = cls.id
    audit(
        db,
        action="student.assign_class",
        actor_user_id=principal.id,
        organization_id=org_id,
        target_type="student",
        target_id=st.id,
        before=before,
        after={"class_id": cls.id, "class_name": cls.name},
    )
    db.commit()
    return {"ok": True, "class_id": cls.id, "class_name": cls.name}
