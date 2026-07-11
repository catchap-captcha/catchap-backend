"""학부모 API — 승인된 연결 자녀만 (require_parent + check_parent_child)."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.permissions import Principal, check_parent_child, require_parent
from app.db.session import get_db
from app.models import (
    ClassRoom,
    Organization,
    ParentStudentLink,
    Report,
    ReportDownloadLog,
    StudentProfile,
)
from app.schemas.parent import ChildSettingsUpdate, ParentProfileUpdate
from app.services import aggregate, onboarding_service
from app.services.aggregate import fb
from pydantic import BaseModel as _BaseModel


class _LinkInviteReq(_BaseModel):
    invite_code: str
from app.services.stats import D  # DB(stat_blobs) 우선, design_data fallback
from app.utils.helpers import audit, status_label

router = APIRouter(tags=["parents"])


def _links(db: Session, parent_id: str) -> list[ParentStudentLink]:
    return (
        db.query(ParentStudentLink)
        .filter(
            ParentStudentLink.parent_user_id == parent_id,
            ParentStudentLink.status == "approved",
        )
        .order_by(ParentStudentLink.created_at)
        .all()
    )


def _child_row(db: Session, link: ParentStudentLink) -> dict | None:
    s = db.get(StudentProfile, link.student_id)
    if s is None:
        return None
    class_name = None
    if s.class_id:
        cls = db.get(ClassRoom, s.class_id)
        class_name = cls.name if cls else None
    org = db.get(Organization, s.organization_id) if s.organization_id else None

    # 정답률/이번 주 학습 횟수: learning_attempts 실집계 — 없으면 D 프리셋(kpis) 유지
    from datetime import date, timedelta

    today = date.today()
    recent = aggregate.attempts(db, student_ids=[s.id], since=today - timedelta(days=28))
    week_rows = [
        r
        for r in recent
        if r.created_at and r.created_at.date() >= today - timedelta(days=today.weekday())
    ]
    preset = D.PARENT_SUMMARY.get(s.nickname, D.PARENT_SUMMARY["하은"])
    correct = sum(1 for r in recent if r.result == "correct")
    accuracy = (
        f"{round(correct / len(recent) * 100)}%" if recent else preset["kpis"][1]["value"]
    )
    week_count = f"{len(week_rows)}회" if recent else preset["kpis"][0]["value"]

    return {
        "id": s.id,
        "nickname": s.nickname,
        "age": s.age,
        "status": status_label(s.status),
        "student_code": s.student_code,
        "class_name": class_name,
        "org_name": org.name if org else None,  # 실테이블(organizations)
        "accuracy": accuracy,
        "week_count": week_count,
        "level": s.level,
        "link_id": link.id,
        "daily_goal": link.daily_goal,
        "time_limit_enabled": link.time_limit_enabled,
    }


@router.get("/parents/me/children")
def children(principal: Principal = Depends(require_parent), db: Session = Depends(get_db)):
    rows = [_child_row(db, link) for link in _links(db, principal.id)]
    return [r for r in rows if r]


@router.patch("/parents/me/profile")
def update_profile(
    req: ParentProfileUpdate,
    principal: Principal = Depends(require_parent),
    db: Session = Depends(get_db),
):
    """학부모 프로필(이름/연락처) — users 실테이블 UPDATE."""
    user = principal.user
    before = {"name": user.name, "phone": user.phone}
    if req.name is not None and req.name.strip():
        user.name = req.name.strip()
    if req.phone is not None:
        user.phone = req.phone
    audit(
        db,
        action="parent.profile_update",
        actor_user_id=principal.id,
        target_type="user",
        target_id=principal.id,
        before=before,
        after={"name": user.name, "phone": user.phone},
    )
    db.commit()
    return {"ok": True, "name": user.name, "phone": user.phone}


def _parent_banner(child: StudentProfile, has_week: bool) -> dict:
    """자녀 이름이 정확히 들어간 요약 배너. 프리셋이 있으면 그 문구, 없으면 이번 주 활동 여부로 생성."""
    preset = D.PARENT_SUMMARY.get(child.nickname)
    if preset:
        return {"title": preset["banner_title"], "body": preset["banner_body"]}
    if has_week:
        return {
            "title": f"{child.nickname} 이번 주 학습이 순조로워요!",
            "body": "이번 주 학습 기록을 아래에서 확인해 보세요.",
        }
    return {
        "title": f"{child.nickname} 이번 주 학습을 응원해요!",
        "body": "아직 이번 주 학습 기록이 많지 않아요. 함께 시작해 볼까요?",
    }


@router.get("/parents/me/children/{child_id}/summary")
def child_summary(
    child_id: str,
    principal: Principal = Depends(require_parent),
    db: Session = Depends(get_db),
):
    check_parent_child(db, principal.id, child_id)
    child = db.get(StudentProfile, child_id)
    if child is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="자녀 정보를 찾을 수 없습니다.")
    # 숫자 폴백은 프리셋 형태를 재사용하되, 화면에 보이는 배너 문구는 자녀 실제 이름으로 생성한다
    # (프리셋 없는 자녀에게 '하은' 배너가 나가던 버그 제거).
    preset = D.PARENT_SUMMARY.get(child.nickname) or D.PARENT_SUMMARY["하은"]
    # KPI/강약점: learning_attempts 실집계 — 이번 주 시도 없으면 D 유지
    sw = aggregate.parent_strengths_weaknesses(db, child) or {}
    week_kpis = aggregate.parent_week_kpis(db, child)
    return {
        "child": {
            "id": child.id,
            "nickname": child.nickname,
            "age": child.age,
            "student_code": child.student_code,
        },
        "status": preset["status"],
        "banner": _parent_banner(child, has_week=bool(week_kpis)),
        "kpis": fb(week_kpis, preset["kpis"]),
        **D.PARENT_SUMMARY_COMMON,
        # 기간 라벨은 오늘 기준 실제 주로, 원인 카드는 자녀 실제 오답 분포로 (없으면 D 유지)
        "period_label": aggregate.period_label("week"),
        "reasons": fb(aggregate.parent_reasons(db, child), D.PARENT_SUMMARY_COMMON["reasons"]),
        "strengths": fb(sw.get("strengths"), D.PARENT_SUMMARY_COMMON["strengths"]),
        "weaknesses": fb(sw.get("weaknesses"), D.PARENT_SUMMARY_COMMON["weaknesses"]),
        # 자녀의 이번 주 실 학습기록이 없어 KPI가 디자인(데모)값이면 demo=True
        "demo": week_kpis is None,
    }


@router.get("/parents/me/children/{child_id}/report")
def child_report(
    child_id: str,
    period: str = Query(default="week"),
    subject: str | None = Query(default=None),
    principal: Principal = Depends(require_parent),
    db: Session = Depends(get_db),
):
    check_parent_child(db, principal.id, child_id)
    child = db.get(StudentProfile, child_id)
    if child is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="자녀 정보를 찾을 수 없습니다.")
    d = D.PARENT_REPORT.get(child.nickname, D.PARENT_REPORT["하은"])
    valid_periods = {"week", "month", "year"}
    period = period if period in valid_periods else "week"

    # 기관·학급 라벨: 실테이블(organizations/classes) 기준
    org = db.get(Organization, child.organization_id) if child.organization_id else None
    cls = db.get(ClassRoom, child.class_id) if child.class_id else None
    org_label = " ".join(x for x in [org.name if org else None, cls.name if cls else None] if x)

    # 과목 선택형 추이 (6주 + 반 평균) — D fallback은 디자인 로직 그대로 계산
    ts = subject if subject in d["subj_last"] else "all"
    base, cbase = d["trend_base"], d["class_base"]
    clamp = lambda v: max(45, min(99, v))  # noqa: E731
    shift = d["subj_last"][ts] - base[-1]
    cshift = d["class_last"][ts] - cbase[-1]
    series = [clamp(v + shift) for v in base]
    cseries = [clamp(v + cshift) for v in cbase]

    # learning_attempts 실집계 — kpis/bars/trend/grade/percentile (없는 항목은 D 유지)
    agg = aggregate.parent_report_overrides(db, child, ts if ts != "all" else None)

    # 강/약점: 과목별 정답률 상하위 (실집계 없으면 D)
    sw = aggregate.parent_strengths_weaknesses(db, child) or {}

    return {
        "child": {
            "id": child.id,
            "nickname": child.nickname,
            "age": child.age,
            "student_code": child.student_code,
        },
        "period": period,
        "period_label": aggregate.period_label(period),
        "org_label": org_label or "햇살초등학교 1-2반",
        "grade": fb(agg.get("grade"), d["grade"]),
        "percentile": fb(agg.get("percentile"), d["percentile"]),
        "kpis": fb(agg.get("kpis"), d["kpis"]),
        "strengths": fb(sw.get("strengths"), d["strengths"]),
        "weaknesses": fb(sw.get("weaknesses"), d["weaknesses"]),
        "strength_note": d["strength_note"],
        "weakness_note": d["weakness_note"],
        "bars": fb(agg.get("bars"), d["bars"]),
        "trend_delta": fb(agg.get("trend_delta"), d["trend_delta"]),
        "trend": {
            "subject": ts,
            "axis": ["5주전", "4주전", "3주전", "2주전", "지난주", "이번주"],
            "series": fb(agg.get("trend_series"), series),
            "class_series": fb(agg.get("trend_class_series"), cseries),
            "avg": fb(agg.get("trend_avg"), round(sum(series) / len(series))),
        },
        "reasons": fb(aggregate.parent_reasons(db, child), D.PARENT_SUMMARY_COMMON["reasons"]),
        "recommendations": D.PARENT_SUMMARY_COMMON["recommendations"],
        # 담임 작성 기능이 없으므로 'AI 양육 가이드'로 명명 (구 teacher_comment — 화면 미표시 필드)
        "ai_comment": d.get("ai_comment", d.get("teacher_comment", "")),
        # 자녀 기간 실집계가 없어 등급·백분위·차트가 디자인(데모)값이면 demo=True
        "demo": not agg,
    }


# NOTE: 학생코드(CAT-####) 자동승인 연결(구 link-request)은 제거됨 (B1 완전 해소).
# 자녀 연결은 학교 발급 초대코드(link-invite)로만 — 아래 link_invite 참고.


@router.delete("/parents/me/children/{child_id}/link")
def unlink(
    child_id: str,
    principal: Principal = Depends(require_parent),
    db: Session = Depends(get_db),
):
    link = check_parent_child(db, principal.id, child_id)
    link.status = "removed"
    audit(
        db,
        action="parent.child_unlink",
        actor_user_id=principal.id,
        organization_id=link.organization_id,
        target_type="parent_student_link",
        target_id=child_id,
        before={"status": "approved"},
        after={"status": "removed"},
    )
    db.commit()
    return {"ok": True}


@router.get("/parents/me/children/{child_id}/settings")
def child_settings(
    child_id: str,
    principal: Principal = Depends(require_parent),
    db: Session = Depends(get_db),
):
    link = check_parent_child(db, principal.id, child_id)
    return {"daily_goal": link.daily_goal, "time_limit_enabled": link.time_limit_enabled}


@router.put("/parents/me/children/{child_id}/settings")
def save_child_settings(
    child_id: str,
    req: ChildSettingsUpdate,
    principal: Principal = Depends(require_parent),
    db: Session = Depends(get_db),
):
    link = check_parent_child(db, principal.id, child_id)
    before = {"daily_goal": link.daily_goal, "time_limit_enabled": link.time_limit_enabled}
    link.daily_goal = req.daily_goal
    link.time_limit_enabled = req.time_limit_enabled
    audit(
        db,
        action="parent.child_settings_update",
        actor_user_id=principal.id,
        organization_id=link.organization_id,
        target_type="parent_student_link",
        target_id=child_id,
        before=before,
        after={"daily_goal": req.daily_goal, "time_limit_enabled": req.time_limit_enabled},
    )
    db.commit()
    return {"ok": True, "daily_goal": link.daily_goal, "time_limit_enabled": link.time_limit_enabled}


# ---------------------------------------------------------------- 리포트
@router.get("/parents/me/reports")
def my_reports(principal: Principal = Depends(require_parent), db: Session = Depends(get_db)):
    child_ids = [link.student_id for link in _links(db, principal.id)]
    if not child_ids:
        return []
    students = {
        s.id: s
        for s in db.query(StudentProfile).filter(StudentProfile.id.in_(child_ids)).all()
    }
    rows = (
        db.query(Report)
        .filter(Report.student_id.in_(child_ids))
        .order_by(Report.created_at.desc())
        .all()
    )
    out = []
    for r in rows:
        child = students.get(r.student_id)
        out.append(
            {
                "id": r.id,
                "child_id": r.student_id,
                "child_nickname": child.nickname if child else None,
                "report_type": r.report_type,
                "period_start": r.period_start.isoformat() if r.period_start else None,
                "period_end": r.period_end.isoformat() if r.period_end else None,
                "status": r.status,
                "file_url": r.file_url,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
        )
    return out


@router.post("/reports/{report_id}/download")
def download_report(
    report_id: str,
    request: Request,
    principal: Principal = Depends(require_parent),
    db: Session = Depends(get_db),
):
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="리포트를 찾을 수 없습니다.")
    # 소유자(student_id)가 없는 리포트는 '공개'가 아니라 '권한 없음'으로 처리 (deny by default)
    if not report.student_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="다운로드 권한이 없습니다.")
    check_parent_child(db, principal.id, report.student_id)
    file_url = report.file_url or f"/files/reports/{report.id}.pdf"  # 파일 생성은 stub
    db.add(
        ReportDownloadLog(
            report_id=report.id,
            user_id=principal.id,
            downloaded_at=datetime.now(),  # created_at 로컬 저장 규약과 통일
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    return {"ok": True, "file_url": file_url}


# ---------------------------------------------------------------- 초대 코드로 자녀 연결 (B1 해소)
@router.post("/parents/me/children/link-invite")
def link_invite(
    req: _LinkInviteReq,
    principal: Principal = Depends(require_parent),
    db: Session = Depends(get_db),
):
    """학교 발급 초대 코드로만 자녀 연결. 임의 학생코드 추측 연결(B1) 대체.

    코드 대입 방지: 학부모 계정 기준 과도한 실패 시 차단(H1).
    """
    from app.services import auth_service as _as

    ident = f"invite:{principal.id}"
    _as._check_locked(db, ident)
    try:
        link = onboarding_service.consume_parent_invite(db, principal.id, req.invite_code)
    except HTTPException as e:
        if e.status_code in (404, 409, 410):
            _as._record_fail(db, ident)
        raise
    _as._reset_fails(db, ident)
    # 학생 화면 연동 알림 팝업용 — 학교 발급 초대코드로 연결됐음을 아이에게 알림
    from app.models import Notification

    parent_name = principal.user.name if principal.user else "보호자"
    db.add(
        Notification(
            student_id=link.student_id,
            organization_id=link.organization_id,
            type="parent_link",
            category="연결",
            title=f"{parent_name} 보호자님과 연결됐어요",
            message=(
                f"{parent_name} 보호자님이 학교에서 발급한 초대코드로 내 계정과 연결됐어요. "
                "이제 보호자님이 나의 학습 현황을 함께 볼 수 있어요. "
                "내가 모르는 연결이라면 선생님께 알려주세요."
            ),
        )
    )
    # 감사 — 학부모가 아동 학습데이터 접근권을 얻는 민감 행위. 해제(parent.child_unlink)와 대칭.
    audit(
        db,
        action="parent.child_link",
        actor_user_id=principal.id,
        organization_id=link.organization_id,
        target_type="parent_student_link",
        target_id=link.student_id,
        after={"via": "invite_code"},
    )
    db.commit()
    return {"ok": True, "student_id": link.student_id}
