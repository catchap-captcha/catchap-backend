"""기관 관리자 API — 자기 기관만 (require_org_admin + check_org_scope)."""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.permissions import (
    Principal,
    check_grade_scope,
    check_org_scope,
    managed_grade,
    require_grade_head,
    require_org_admin,
)
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
    StudentJoinCode,
    StudentProfile,
    Subscription,
    User,
)
import secrets as _secrets

from app.core.security import hash_password as _hash_password
from app.schemas.org import (
    AppointGradeHead,
    CaptchaSettingsUpdate,
    OrgUpdate,
    TeacherCreate,
    TeacherInviteCreate,
    TeacherUpdate,
)
from app.services import aggregate, invite_service, onboarding_service
from app.services import auth_service as _auth_service
from app.services.aggregate import fb
from pydantic import BaseModel as _BaseModel


class _RegisterStudentsReq(_BaseModel):
    count: int = 1
    class_label: str | None = None
    class_id: str | None = None
    names: list[str] | None = None  # 학생 실명(슬롯 순서대로, 교사·기관 화면 전용)
    genders: list[str | None] | None = None  # 성별(슬롯 순서대로, 선생님 입력 — 아이가 안 고름)
from app.services.stats import D  # DB(stat_blobs) 우선, design_data fallback
from app.utils.helpers import audit, parse_grade, student_display_name, summary_acc, utc_to_local

router = APIRouter(prefix="/orgs", tags=["orgs"])


def _scope_grade(db: Session, principal: Principal) -> int | None:
    """이 요청의 학년 범위. None=전 학년(교장/운영), 정수=해당 학년만(학년부장).

    학년부장인데 담당 학년이 지정 안 됐으면 403 (관리 대상 없음).
    """
    if principal.role == "grade_head":
        mg = managed_grade(db, principal)
        if mg is None:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail="담당 학년이 지정되지 않았습니다."
            )
        return mg
    return None


def _class_grade(cls: ClassRoom) -> int | None:
    """학급의 학년 — 컬럼 우선, 없으면 이름에서 파싱."""
    return cls.grade if cls.grade is not None else parse_grade(cls.name)


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
        # 만료는 UTC 저장 — 사용자 노출은 KST 벽시계로 변환(다른 시각들과 규약 통일)
        "code_expires_at": (
            utc_to_local(org.code_expires_at).isoformat() if org.code_expires_at else None
        ),
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


# ---------------------------------------------------------------- 기관 코드 재발급 (연 1회 갱신)
@router.post("/{org_id}/rotate-code")
def rotate_org_code(
    org_id: str,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """기관 가입 코드 재발급 — 새 코드 발급 + 만료일 1년 연장. 교장(org_admin) 전용.

    유출·학년 교체 시 기존 코드를 무효화하고 새 코드를 배부한다.
    """
    check_org_scope(principal, org_id)
    org = _org(db, org_id)
    from app.services.auth_service import _generate_org_code

    old = org.code
    org.code = _generate_org_code(db, org.name)  # 유일 보장(기존 코드와 다름)
    org.code_expires_at = datetime.utcnow() + timedelta(days=365)
    audit(
        db,
        action="org.code_rotate",
        actor_user_id=principal.id,
        organization_id=org_id,
        target_type="organization",
        target_id=org_id,
        before={"code": old},
        after={"code": org.code, "code_expires_at": org.code_expires_at.isoformat()},
    )
    db.commit()
    return {
        "ok": True,
        "code": org.code,
        "code_expires_at": utc_to_local(org.code_expires_at).isoformat(),  # 노출은 KST
        "code_remain_days": 365,
    }


# ---------------------------------------------------------------- 대시보드/분석
@router.get("/{org_id}/dashboard")
def dashboard(
    org_id: str,
    period: str = Query(default="week"),
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    # 전교 집계라 교장 전용 (학년부장은 담당 학년 밖 데이터 열람 불가 — 학급·학생 화면으로 스코프)
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
        # 학습 실집계(학급별)가 없어 정답률·학급표·그래프가 디자인(데모)값이면 demo=True
        "demo": not extras.get("classes"),
    }


@router.get("/{org_id}/analytics")
def analytics(
    org_id: str,
    period: str = Query(default="week"),
    subject: str | None = Query(default=None),
    principal: Principal = Depends(require_org_admin),  # 전교 집계 — 교장 전용
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
        # 학습 실집계가 없어 정답률 시리즈·과목·학급 표가 디자인(데모)값이면 demo=True
        "demo": not agg and not extras.get("classes"),
    }


# ---------------------------------------------------------------- 학급/roster
@router.get("/{org_id}/classes")
def classes(
    org_id: str,
    principal: Principal = Depends(require_grade_head),
    db: Session = Depends(get_db),
):
    check_org_scope(principal, org_id)
    scope_grade = _scope_grade(db, principal)  # 학년부장이면 자기 학년만
    rows = (
        db.query(ClassRoom)
        .filter(ClassRoom.organization_id == org_id, ClassRoom.status == "active")
        .order_by(ClassRoom.name)
        .all()
    )
    if scope_grade is not None:
        rows = [c for c in rows if _class_grade(c) == scope_grade]
    design = {c["name"]: c for c in D.ORG_CLASSES}
    # 학급별 학생 수·명단(정답률 평균용)을 한 번의 조회로 함께 구성 — 같은 테이블 이중 스캔 방지.
    # 실 정답률: 반 학생들의 28일 정답률 평균(learning_attempts 실집계). 없으면 디자인 폴백.
    counts: dict[str, int] = {}
    cls_students: dict[str, list[str]] = {}
    for sid, cid in (
        db.query(StudentProfile.id, StudentProfile.class_id)
        .filter(
            StudentProfile.organization_id == org_id,
            StudentProfile.class_id.isnot(None),
            StudentProfile.status != "disabled",
        )
        .all()
    ):
        counts[cid] = counts.get(cid, 0) + 1
        cls_students.setdefault(cid, []).append(sid)
    metrics = aggregate.student_roster_metrics(db, [s for lst in cls_students.values() for s in lst])

    def _class_acc(cid: str) -> int | None:
        accs = [metrics[s]["acc"] for s in cls_students.get(cid, []) if s in metrics]
        return round(sum(accs) / len(accs)) if accs else None

    # 담임·보조 담임 사용자 — 반마다 개별 조회하지 않고 배치 로드
    staff_ids = {c.teacher_id for c in rows if c.teacher_id} | {
        c.assistant_teacher_id for c in rows if c.assistant_teacher_id
    }
    staff_by_id = {
        u.id: u for u in db.query(User).filter(User.id.in_(staff_ids or [""])).all()
    }
    out = []
    for c in rows:
        d = design.get(c.name, {})
        teacher_user = staff_by_id.get(c.teacher_id) if c.teacher_id else None
        assistant_user = staff_by_id.get(c.assistant_teacher_id) if c.assistant_teacher_id else None
        real_count = int(counts.get(c.id, 0))
        real_acc = _class_acc(c.id)  # 실플레이 학생이 있으면 실정답률
        acc = real_acc if real_acc is not None else d.get("acc", 0)
        out.append(
            {
                "id": c.id,
                "key": d.get("key", c.name.replace("반", "")),
                "name": c.name,
                "grade": c.grade,
                # 담당 교사: 실테이블(classes.teacher_id → users) 우선
                "teacher": teacher_user.name if teacher_user else d.get("teacher", "미배정"),
                # 보조 담임(결원 대체) — 없으면 null
                "assistant": assistant_user.name if assistant_user else None,
                # 학생 수: 실테이블 우선 (배정 학생이 없으면 디자인 수치 유지)
                "count": real_count or d.get("count", 0),
                # 정답률·위험도: 반 학생 실집계 (없으면 디자인 폴백), demo 플래그로 표기
                "acc": acc,
                "risk": ("주의" if acc < 75 else "낮음") if real_acc is not None else d.get("risk", "낮음"),
                "demo": real_acc is None,
            }
        )
    return out


class _CreateClassReq(_BaseModel):
    name: str  # "1-2반" 등 — 맨 앞 숫자가 학년


@router.post("/{org_id}/classes")
def create_class(
    org_id: str,
    req: _CreateClassReq,
    principal: Principal = Depends(require_grade_head),
    db: Session = Depends(get_db),
):
    """새 학급 생성 (교장=전 학년, 학년부장=담당 학년만). 반 이름 맨 앞 숫자가 학년.

    이미 있는 활성 반이면 409. 예전에 해체(archived)된 같은 이름 반은 되살린다.
    """
    check_org_scope(principal, org_id)
    scope_grade = _scope_grade(db, principal)
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="반 이름이 필요합니다.")
    grade = parse_grade(name)
    # 학년부장: 담당 학년 반만 (파싱 불가/불일치 fail-closed)
    if scope_grade is not None and grade != scope_grade:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=f"{scope_grade}학년 담당이라 담당 학년 반(예: {scope_grade}-1반)만 만들 수 있어요.",
        )
    existing = (
        db.query(ClassRoom)
        .filter(ClassRoom.organization_id == org_id, ClassRoom.name == name)
        .first()
    )
    if existing is not None:
        if existing.status == "active":
            raise HTTPException(status.HTTP_409_CONFLICT, detail="이미 있는 반 이름이에요.")
        existing.status = "active"  # 해체됐던 반 재사용
        existing.grade = grade
        # 해체 전 담임/보조 연결은 되살리지 않는다 (예전 교사가 부활한 반에 다시 붙는 것 방지)
        existing.teacher_id = None
        existing.assistant_teacher_id = None
        cls = existing
    else:
        cls = ClassRoom(organization_id=org_id, name=name, grade=grade, status="active")
        db.add(cls)
        db.flush()
    audit(
        db,
        action="org.class_create",
        actor_user_id=principal.id,
        organization_id=org_id,
        target_type="class",
        target_id=cls.id,
        after={"name": name, "grade": grade},
    )
    db.commit()
    return {"ok": True, "class": {"id": cls.id, "name": cls.name, "grade": cls.grade}}


@router.delete("/{org_id}/classes/{class_id}")
def dissolve_class(
    org_id: str,
    class_id: str,
    principal: Principal = Depends(require_grade_head),
    db: Session = Depends(get_db),
):
    """학급 해체 (학년말). 교장=전 학년, 학년부장=담당 학년만.

    배정된 학생이 있으면 409 — 먼저 담임이 학생을 다른 반으로 옮기거나 빼야 한다.
    소프트 해체(status=archived) + 담임 연결 해제. 같은 이름으로 다시 만들면 되살아난다.
    """
    check_org_scope(principal, org_id)
    cls = db.get(ClassRoom, class_id)
    if cls is None or cls.organization_id != org_id or cls.status != "active":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="학급을 찾을 수 없습니다.")
    check_grade_scope(db, principal, org_id, _class_grade(cls))  # 학년 범위(파싱불가면 거부)
    count = (
        db.query(StudentProfile)
        .filter(StudentProfile.class_id == cls.id, StudentProfile.status != "disabled")
        .count()
    )
    if count > 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "message": f"{cls.name}에 학생 {count}명이 남아 있어 해체할 수 없어요. 먼저 학생을 다른 반으로 옮겨 주세요.",
                "count": count,
                "cls": cls.name,
            },
        )
    before = {"name": cls.name, "grade": cls.grade, "teacher_id": cls.teacher_id, "status": "active"}
    cls.status = "archived"
    cls.teacher_id = None  # 학년말 담임·보조 연결 모두 해제 (다음 학년 재배정 준비)
    cls.assistant_teacher_id = None
    audit(
        db,
        action="org.class_dissolve",
        actor_user_id=principal.id,
        organization_id=org_id,
        target_type="class",
        target_id=cls.id,
        before=before,
        after={"status": "archived"},
    )
    db.commit()
    return {"ok": True}


def _acc_pct(summary) -> int:
    return summary_acc(summary)


def _roster_display_name(s: StudentProfile) -> str:
    """기관 화면 표시 이름 — 공용 로직(helpers.student_display_name) 사용."""
    return student_display_name(s, D.CODE_FULL_NAME)


@router.get("/{org_id}/roster")
def roster(
    org_id: str,
    cls: str | None = Query(default=None),
    principal: Principal = Depends(require_grade_head),
    db: Session = Depends(get_db),
):
    check_org_scope(principal, org_id)
    scope_grade = _scope_grade(db, principal)  # 학년부장이면 자기 학년 학생만
    # 기관 학생 명단 — 실테이블(student_profiles/classes) 기준.
    # 교장(scope_grade=None)은 '미배정'(학급에서 빠진, class_id=None) 학생도 봐야 다시 배정할 수 있다.
    # (학급 '삭제'는 계정 삭제가 아니라 class_id=None 언링크일 뿐 → 안 보이면 학생이 사라진 것처럼 보인다)
    # 학년부장은 학년 판단이 안 되는 미배정 학생을 볼 수 없으므로 배정된 학생만.
    _students_q = db.query(StudentProfile).filter(
        StudentProfile.organization_id == org_id,
        StudentProfile.status != "disabled",
    )
    if scope_grade is not None:
        _students_q = _students_q.filter(StudentProfile.class_id.isnot(None))
    students = _students_q.order_by(StudentProfile.student_code).all()
    all_classes = db.query(ClassRoom).filter(ClassRoom.organization_id == org_id).all()
    class_names = {c.id: c.name for c in all_classes}
    grade_by_class = {c.id: _class_grade(c) for c in all_classes}
    summaries = {
        r.student_id: r
        for r in db.query(LearningSummary)
        .filter(
            LearningSummary.organization_id == org_id,
            LearningSummary.period_type == "week",
        )
        .all()
    }
    # learning_attempts 실집계 — 실제 푸는 학생은 실정답률, 미플레이/데모는 seed 폴백
    real = aggregate.student_roster_metrics(db, [s.id for s in students])
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
        # 학년부장: 담당 학년 학생만
        if scope_grade is not None and grade_by_class.get(s.class_id) != scope_grade:
            continue
        if cls and cls_name != cls:
            continue
        acc = (real[s.id]["acc"] if s.id in real else _acc_pct(summaries.get(s.id))) or meta.get("acc", 0)
        out.append(
            {
                "id": s.id,
                "name": _roster_display_name(s),
                "nickname": s.nickname,
                # 로그인 아이디(자격증명)는 기관 관리자 화면에 노출하지 않는다(데이터 최소화).
                # 동명이인 구분은 학생 코드(code)로 — 로그인 아이디가 아니라 식별 번호.
                "age": s.age,
                "gender": s.gender,
                "cls": cls_name,
                "code": s.student_code,
                "status": s.status,  # active | pending 등 (학생관리 상태 표시)
                "link": s.id in linked_ids or bool(meta.get("link")),
                "acc": acc,
                "risk": meta.get("risk") or ("주의" if acc < 75 else "낮음"),
                # 실플레이 없어 정답률이 seed/디자인 폴백인 행 = 데모칸
                "demo": s.id not in real,
            }
        )
    # 미가입(코드 발급했지만 아직 활성화 안 함) 학생도 '가입 대기'로 명단에 포함한다.
    # StudentProfile은 활성화(코드 소비) 시 생성되므로, 그 전에는 StudentJoinCode(used_at=None)만 존재.
    # 이게 없으면 코드만 발급하고 아직 안 들어온 학생이 명단에 안 보여 '가입 대기 0'으로 뜬다.
    pending_codes = (
        db.query(StudentJoinCode)
        .filter(
            StudentJoinCode.organization_id == org_id,
            StudentJoinCode.used_at.is_(None),
        )
        .order_by(StudentJoinCode.created_at)
        .all()
    )
    for jc in pending_codes:
        pc_cls = class_names.get(jc.class_id) if jc.class_id else jc.class_label
        pc_grade = (
            grade_by_class.get(jc.class_id)
            if jc.class_id and jc.class_id in grade_by_class
            else (parse_grade(pc_cls) if pc_cls else None)
        )
        if scope_grade is not None and pc_grade != scope_grade:
            continue
        if cls and pc_cls != cls:
            continue
        out.append(
            {
                # 활성화 전이라 학생 엔드포인트 대상이 아님 — 'jc-' 접두사로 프론트가 학생 액션을 가드한다.
                "id": f"jc-{jc.id}",
                "name": jc.real_name or "(이름 미입력)",
                "nickname": None,
                # 로그인 아이디는 기관 화면에 노출 안 함(데이터 최소화)
                "age": None,
                "gender": jc.gender,
                "cls": pc_cls,
                # 코드 원문은 hash만 저장돼 재열람 불가 — 발급 시 1회 확인. 대기 상태만 표시한다.
                "code": None,
                "status": "pending",
                "link": False,
                "acc": 0,
                "risk": "낮음",
                "demo": False,
                "pending_signup": True,
            }
        )

    if scope_grade is None:
        total = (
            db.query(StudentProfile)
            .filter(StudentProfile.organization_id == org_id, StudentProfile.status != "disabled")
            .count()
        )
    else:
        # 학년부장: 담당 학년 학급에 배정된 학생 수만 (전교 수치 노출 안 함)
        grade_ids = {c.id for c in all_classes if _class_grade(c) == scope_grade}
        total = sum(1 for s in students if s.class_id in grade_ids)
    org = _org(db, org_id)
    # 헤더 요약용 실카운트 (classes/memberships 실테이블) — 학년부장은 담당 학년만
    grade_class_ids = {c.id for c in all_classes if c.status == "active"
                       and (scope_grade is None or _class_grade(c) == scope_grade)}
    class_count = len(grade_class_ids)
    if scope_grade is None:
        teacher_count = (
            db.query(Membership)
            .filter(
                Membership.organization_id == org_id,
                Membership.role.in_(("teacher", "grade_head")),
                Membership.status != "disabled",
            )
            .count()
        )
    else:
        # 담당 학년 학급의 담임 교사 수 (그 학년 반을 맡은 교사)
        teacher_ids = {
            c.teacher_id for c in all_classes
            if c.teacher_id and _class_grade(c) == scope_grade
        }
        teacher_count = len(teacher_ids)
    return {
        "total": total,
        "shown": len(out),
        "students": out,
        "org_join_code": org.code,
        "class_count": class_count,
        "teacher_count": teacher_count,
    }


# ---------------------------------------------------------------- 선생님 관리
def _link_homeroom(
    db: Session, org_id: str, class_name: str, user_id: str, scope_grade: int | None
) -> None:
    """'담임' 교사를 해당 반의 담당 교사(classes.teacher_id)로 연결.

    반이 없으면 학년 정보와 함께 생성. 역할(담임) → 실제 학급 소유 권한 반영.
    """
    if not class_name:
        return
    cls = (
        db.query(ClassRoom)
        .filter(ClassRoom.organization_id == org_id, ClassRoom.name == class_name)
        .first()
    )
    grade = scope_grade if scope_grade is not None else parse_grade(class_name)
    if cls is None:
        cls = ClassRoom(
            organization_id=org_id, name=class_name, grade=grade, status="active"
        )
        db.add(cls)
        db.flush()
    elif scope_grade is not None and _class_grade(cls) != scope_grade:
        # 이름은 담당 학년으로 보여도 실제 학년(컬럼)이 다르면 거부 (이름/학년 불일치 방어)
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="담당 학년 반이 아닙니다.")
    # 담임은 반당 1명 — 이미 다른 (해제 안 된) 담임이 있으면 거부. 같은 교사 재배정·해제된 담임 교체는 허용.
    if cls.teacher_id and cls.teacher_id != user_id:
        existing = db.get(User, cls.teacher_id)
        if existing is not None and existing.status != "disabled":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"{class_name}에는 이미 담임 선생님이 있어요. 먼저 기존 담임을 다른 반으로 옮기거나 해제해 주세요.",
            )
    cls.teacher_id = user_id


def _link_assistant(
    db: Session, org_id: str, class_name: str, user_id: str, scope_grade: int | None
) -> None:
    """'보조'/'대체' 교사를 해당 반의 보조 담임(classes.assistant_teacher_id)으로 연결.

    담임 결원 시 이 교사가 반을 대신 볼 수 있다. 반이 없으면 생성.
    """
    if not class_name:
        return
    cls = (
        db.query(ClassRoom)
        .filter(ClassRoom.organization_id == org_id, ClassRoom.name == class_name)
        .first()
    )
    grade = scope_grade if scope_grade is not None else parse_grade(class_name)
    if cls is None:
        cls = ClassRoom(
            organization_id=org_id, name=class_name, grade=grade, status="active"
        )
        db.add(cls)
        db.flush()
    elif scope_grade is not None and _class_grade(cls) != scope_grade:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="담당 학년 반이 아닙니다.")
    cls.assistant_teacher_id = user_id


def _teacher_row(
    db: Session,
    m: Membership,
    users_by_id: dict[str, User] | None = None,
    cls_by_user: dict[str, ClassRoom] | None = None,
) -> dict:
    """교사 행 1개. 목록처럼 여러 행을 만들 때는 users_by_id/cls_by_user를 미리 만들어
    넘겨 행마다 개별 조회(N+1)하지 않는다. 단건 응답(add/update)은 db 조회 폴백."""
    if m.user_id:
        user = users_by_id.get(m.user_id) if users_by_id is not None else db.get(User, m.user_id)
    else:
        user = None
    # 담당 학급: 담임(teacher_id) 또는 보조(assistant_teacher_id) 어느 쪽으로든 연결된 반
    if not m.user_id:
        cls = None
    elif cls_by_user is not None:
        cls = cls_by_user.get(m.user_id)
    else:
        cls = (
            db.query(ClassRoom)
            .filter(
                ClassRoom.status == "active",
                (ClassRoom.teacher_id == m.user_id)
                | (ClassRoom.assistant_teacher_id == m.user_id),
            )
            .order_by(ClassRoom.name)
            .first()
        )
    design = next((t for t in D.ORG_TEACHERS if user and t["name"] == user.name), None)
    # 역할 표기: 학년부장은 담당 학년까지, 그 외엔 관리자가 배정한 직책(담임/교과/보조)이 있으면
    # 그 값, 없으면(초대 가입 직후·미배정) 시스템 역할 그대로 '교사'.
    # (이름은 항상 placeholder User에 보관 — position은 직책 전용이라 이름이 섞이지 않는다.)
    if m.role == "grade_head":
        role_label = (f"{m.managed_grade}학년 " if m.managed_grade else "") + "학년부장"
    elif m.position:
        role_label = m.position
    else:
        role_label = "교사"
    return {
        "id": m.id,
        "user_id": m.user_id,
        "name": user.name if user else "미등록",
        "email": user.email if user else None,
        # 실제 연결된 반이 없으면(미가입 초대 교사) 예약된 담당 반(pending_class)을 보여준다.
        "cls": cls.name if cls else (m.pending_class or (design["cls"] if design else None)),
        "role": role_label,
        "code": m.teacher_code,
        "years": m.career_years or 0,
        "status": "active" if m.status == "active" else "pending",
        # 학년부장 여부 + 담당 학년 (교장 화면 배지/관리용)
        "is_grade_head": m.role == "grade_head",
        "managed_grade": m.managed_grade,
        # 담당 학급의 학년 (그 교사가 속한 학년 — 학년부장 범위 판단용)
        "grade": _class_grade(cls) if cls else None,
    }


@router.get("/{org_id}/teachers")
def teachers(
    org_id: str,
    principal: Principal = Depends(require_grade_head),
    db: Session = Depends(get_db),
):
    check_org_scope(principal, org_id)
    scope_grade = _scope_grade(db, principal)  # 학년부장이면 자기 학년 교사만
    rows = (
        db.query(Membership)
        .filter(
            Membership.organization_id == org_id,
            Membership.role.in_(("teacher", "grade_head")),
            Membership.status != "disabled",
        )
        .order_by(Membership.created_at)
        .all()
    )
    # 행마다 사용자·학급을 조회하지 않도록(N+1) 배치 프리페치
    uids = {m.user_id for m in rows if m.user_id}
    users_by_id = {
        u.id: u for u in db.query(User).filter(User.id.in_(uids or [""])).all()
    }
    cls_by_user: dict[str, ClassRoom] = {}
    for c in (
        db.query(ClassRoom)
        .filter(
            ClassRoom.status == "active",
            ClassRoom.teacher_id.in_(uids or [""])
            | ClassRoom.assistant_teacher_id.in_(uids or [""]),
        )
        .order_by(ClassRoom.name)
        .all()
    ):
        for uid in (c.teacher_id, c.assistant_teacher_id):
            if uid in uids and uid not in cls_by_user:
                cls_by_user[uid] = c
    out = [_teacher_row(db, m, users_by_id, cls_by_user) for m in rows]
    if scope_grade is not None:
        # 담당 학년 학급을 맡은 교사 + 담당 학년으로 지정된 학년부장만
        out = [
            t for t in out
            if t["grade"] == scope_grade or t["managed_grade"] == scope_grade
        ]
    return out


@router.post("/{org_id}/teachers")
def add_teacher(
    org_id: str,
    req: TeacherCreate,
    principal: Principal = Depends(require_grade_head),
    db: Session = Depends(get_db),
):
    check_org_scope(principal, org_id)
    scope_grade = _scope_grade(db, principal)
    # 학년부장은 자기 학년 반의 교사만 추가 가능 (파싱 불가한 반 이름도 fail-closed로 거부)
    if scope_grade is not None and req.class_name:
        cg = parse_grade(req.class_name)
        if cg != scope_grade:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail=f"{scope_grade}학년 담당이라 담당 학년 반(예: {scope_grade}-1반)의 교사만 추가할 수 있어요.",
            )
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
    # 역할→권한 실제 반영: 담임은 담당 교사, 보조는 보조 담임(결원 대체)으로 반에 연결.
    # 계정이 아직 없으면(코드만 발급) pending_class에 예약 → 가입(코드 클레임) 시 자동 배정.
    if req.class_name:
        cname = req.class_name.strip()
        if membership.user_id:
            if req.role == "담임":
                _link_homeroom(db, org_id, cname, membership.user_id, scope_grade)
            elif req.role == "보조":
                _link_assistant(db, org_id, cname, membership.user_id, scope_grade)
        elif req.role in ("담임", "보조"):
            membership.pending_class = cname
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


@router.post("/{org_id}/teacher-invites")
def invite_teacher(
    org_id: str,
    req: TeacherInviteCreate,
    principal: Principal = Depends(require_grade_head),
    db: Session = Depends(get_db),
):
    """교사 초대링크 발송 — 교사코드 선발급 + 이메일 발송. 링크 클릭 시 가입화면에 기관·코드 프리필."""
    check_org_scope(principal, org_id)
    # 담임으로 미리 배정할 반이 이미 담임이 있으면 예약 자체를 막는다(반당 담임 1명).
    if req.class_name and req.class_name.strip():
        cls = (
            db.query(ClassRoom)
            .filter(ClassRoom.organization_id == org_id, ClassRoom.name == req.class_name.strip())
            .first()
        )
        if cls and cls.teacher_id:
            existing = db.get(User, cls.teacher_id)
            if existing is not None and existing.status != "disabled":
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail=f"{req.class_name.strip()}에는 이미 담임 선생님이 있어요. 다른 반을 고르거나 기존 담임을 먼저 옮겨 주세요.",
                )
    invite_service.create_teacher_invite(
        db,
        organization_id=org_id,
        inviter_id=principal.id,
        email=req.email,
        name=req.name,
        role=req.role,
        class_name=req.class_name,
    )
    audit(
        db,
        action="org.teacher_invite",
        actor_user_id=principal.id,
        organization_id=org_id,
        target_type="invitation",
        target_id=None,
        after={"email": req.email, "role": req.role, "class": req.class_name},
    )
    db.commit()
    # 토큰은 메일로만 전달 — 응답에 노출하지 않는다.
    return {"ok": True, "email": req.email}


@router.patch("/{org_id}/teachers/{teacher_id}")
def update_teacher(
    org_id: str,
    teacher_id: str,
    req: TeacherUpdate,
    principal: Principal = Depends(require_grade_head),
    db: Session = Depends(get_db),
):
    check_org_scope(principal, org_id)
    scope_grade = _scope_grade(db, principal)
    m = db.get(Membership, teacher_id)
    if m is None or m.organization_id != org_id or m.role not in ("teacher", "grade_head"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="선생님을 찾을 수 없습니다.")
    before = _teacher_row(db, m)
    # 학년부장은 자기 학년 교사만 수정. 학급 미배정(grade=None) 교사는 학년을 알 수 없으므로
    # 학년부장에게는 fail-closed(거부) — 미배정 교사 관리는 교장 몫. (학년부장 본인은 managed_grade로 판정)
    if scope_grade is not None:
        target_grade = before["grade"] if before["grade"] is not None else before["managed_grade"]
        if target_grade != scope_grade:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="담당 학년 교사가 아닙니다.")
    if req.class_name and scope_grade is not None:
        cg = parse_grade(req.class_name)
        if cg != scope_grade:  # 파싱 불가한 반 이름도 거부(fail-closed)
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail="담당 학년 반으로만 지정할 수 있습니다."
            )
    if req.role is not None:
        m.position = req.role
    # 담임/보조 + 반 지정 시 담당 학급 연결 (역할→권한 실제 반영)
    effective_role = req.role or m.position
    if req.class_name and m.user_id:
        if effective_role == "담임":
            _link_homeroom(db, org_id, req.class_name.strip(), m.user_id, scope_grade)
        elif effective_role == "보조":
            _link_assistant(db, org_id, req.class_name.strip(), m.user_id, scope_grade)
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
    principal: Principal = Depends(require_grade_head),
    db: Session = Depends(get_db),
):
    check_org_scope(principal, org_id)
    scope_grade = _scope_grade(db, principal)
    m = db.get(Membership, teacher_id)
    if m is None or m.organization_id != org_id or m.role not in ("teacher", "grade_head"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="선생님을 찾을 수 없습니다.")
    # 학년부장은 자기 학년 교사만 삭제 (미배정 grade=None 교사는 fail-closed로 거부)
    if scope_grade is not None:
        row = _teacher_row(db, m)
        target_grade = row["grade"] if row["grade"] is not None else row["managed_grade"]
        if target_grade != scope_grade:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="담당 학년 교사가 아닙니다.")
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
    m.managed_grade = None
    # 담임/보조로 연결돼 있던 반의 링크 정리 (사라진 교사가 반에 남지 않도록)
    if m.user_id:
        for c in db.query(ClassRoom).filter(ClassRoom.teacher_id == m.user_id).all():
            c.teacher_id = None
        for c in db.query(ClassRoom).filter(ClassRoom.assistant_teacher_id == m.user_id).all():
            c.assistant_teacher_id = None
        # 삭제된 교사가 계속 로그인·전교생 조회하지 못하도록 계정 비활성 + 토큰 폐기(디프로비저닝).
        # 단, 다른 기관에 아직 유효한 멤버십이 있으면 계정은 살려둔다.
        u = db.get(User, m.user_id)
        other = (
            db.query(Membership)
            .filter(
                Membership.user_id == m.user_id,
                Membership.id != m.id,
                Membership.status != "disabled",
            )
            .first()
        )
        if u and other is None:
            u.status = "disabled"
            if u.role == "grade_head":
                u.role = "teacher"
        _auth_service.logout(db, m.user_id)  # refresh 토큰 폐기 → 모든 기기 로그아웃
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


# ---------------------------------------------------------------- 학년부장 임명/해임 (교장 전용)
@router.get("/{org_id}/grade-heads")
def grade_heads(
    org_id: str,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """현재 학년부장 목록 (교장 화면). 학년별 담당자 확인용."""
    check_org_scope(principal, org_id)
    rows = (
        db.query(Membership)
        .filter(
            Membership.organization_id == org_id,
            Membership.role == "grade_head",
            Membership.status != "disabled",
        )
        .order_by(Membership.managed_grade)
        .all()
    )
    return [_teacher_row(db, m) for m in rows]


@router.post("/{org_id}/teachers/{teacher_id}/grade-head")
def appoint_grade_head(
    org_id: str,
    teacher_id: str,
    req: AppointGradeHead,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """교사를 학년부장으로 임명 — 담당 학년 지정. 교장(org_admin) 전용.

    User.role/Membership.role 를 grade_head 로 승격 + managed_grade 설정.
    한 학년에 학년부장은 1명 — 기존 담당자가 있으면 교체(기존자는 교사로 강등).
    """
    check_org_scope(principal, org_id)
    m = db.get(Membership, teacher_id)
    if m is None or m.organization_id != org_id or m.role not in ("teacher", "grade_head"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="선생님을 찾을 수 없습니다.")
    if m.user_id is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="아직 가입하지 않은 교사는 임명할 수 없습니다."
        )
    # 같은 학년 기존 학년부장 강등 (1학년 1명 규칙)
    prev = (
        db.query(Membership)
        .filter(
            Membership.organization_id == org_id,
            Membership.role == "grade_head",
            Membership.managed_grade == req.grade,
            Membership.id != m.id,
            Membership.status != "disabled",
        )
        .all()
    )
    for p in prev:
        p.role = "teacher"
        p.managed_grade = None
        pu = db.get(User, p.user_id) if p.user_id else None
        if pu and pu.role == "grade_head":
            pu.role = "teacher"
    before = {"role": m.role, "managed_grade": m.managed_grade}
    m.role = "grade_head"
    m.managed_grade = req.grade
    user = db.get(User, m.user_id)
    if user:
        user.role = "grade_head"  # 로그인 시 학년부장 콘솔로 진입
    audit(
        db,
        action="org.grade_head_appoint",
        actor_user_id=principal.id,
        organization_id=org_id,
        target_type="membership",
        target_id=m.id,
        before=before,
        after={"role": "grade_head", "managed_grade": req.grade},
    )
    db.commit()
    return {"ok": True, "teacher": _teacher_row(db, m)}


@router.delete("/{org_id}/teachers/{teacher_id}/grade-head")
def dismiss_grade_head(
    org_id: str,
    teacher_id: str,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """학년부장 해임 → 일반 교사로 강등. 교장 전용."""
    check_org_scope(principal, org_id)
    m = db.get(Membership, teacher_id)
    if m is None or m.organization_id != org_id or m.role != "grade_head":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="학년부장을 찾을 수 없습니다.")
    before = {"role": m.role, "managed_grade": m.managed_grade}
    m.role = "teacher"
    m.managed_grade = None
    user = db.get(User, m.user_id) if m.user_id else None
    if user and user.role == "grade_head":
        user.role = "teacher"
    audit(
        db,
        action="org.grade_head_dismiss",
        actor_user_id=principal.id,
        organization_id=org_id,
        target_type="membership",
        target_id=m.id,
        before=before,
        after={"role": "teacher", "managed_grade": None},
    )
    db.commit()
    return {"ok": True, "teacher": _teacher_row(db, m)}


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
    active_keys = (
        db.query(func.count(ApiKey.id))
        .filter(ApiKey.organization_id == org_id, ApiKey.status == "active")
        .scalar()
        or 0
    )
    return {
        "status": "정상",
        "message": "모든 서비스 정상 작동 중",
        "site_key": masked,
        "domain": site.domain if site else None,
        # 실집계만 반환 — 사용 이력이 없는 신규 기관에 데모 수치(3912/86540/0.3%/142ms)를
        # 보여주지 않는다. 값이 없으면 0/None으로, 프론트가 빈/0으로 렌더한다.
        "calls_today": today_n,
        "calls_month": month_n,
        "error_rate": error_rate,
        "avg_latency_ms": avg_latency,
        "active_keys": active_keys,
        # 과목별 이번 달 교육형 호출 — 대시보드 과목별 사용량 위젯
        "subject_usage": aggregate.subject_usage_this_month(db, org_id),
    }


@router.get("/{org_id}/site-status")
def site_status(
    org_id: str,
    principal: Principal = Depends(require_grade_head),  # 읽기 전용 위젯 — 학년부장도 조회
    db: Session = Depends(get_db),
):
    check_org_scope(principal, org_id)
    return _site_status_payload(db, org_id)


# ---------------------------------------------------------------- 사이드바 위젯
@router.get("/{org_id}/sidebar")
def sidebar(
    org_id: str,
    principal: Principal = Depends(require_grade_head),  # 공용 레이아웃 위젯 — 학년부장도 조회
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
    principal: Principal = Depends(require_grade_head),
    db: Session = Depends(get_db),
):
    """학생 슬롯 N개 생성 + 1회용 가입 코드 발급. 코드 원문은 이 응답에서만 노출.

    학년부장은 자기 담당 학년의 반으로만 학생을 등록할 수 있다.
    """
    check_org_scope(principal, org_id)
    scope_grade = _scope_grade(db, principal)
    resolved_class_id = req.class_id
    # 타 기관 소속 class_id를 주입해 학생을 남의 학급에 귀속시키는 것을 차단
    if req.class_id:
        cls = db.get(ClassRoom, req.class_id)
        if cls is None or cls.organization_id != org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="학급을 찾을 수 없습니다.")
        check_grade_scope(db, principal, org_id, _class_grade(cls))
    elif req.class_label and req.class_label.strip():
        # 반 이름만 준 경우: 실제 학급으로 연결(find-or-create)해야 활성화 시 학생이 그 반에 배정됨.
        # (예전엔 class_label만 코드에 저장하고 class_id=None → 학생이 반 없이 활성화되는 누락이 있었음)
        label = req.class_label.strip()
        target_grade = parse_grade(label)
        if scope_grade is not None and target_grade != scope_grade:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail=f"{scope_grade}학년 담당이라 담당 학년 반(예: {scope_grade}-1반)을 지정해야 학생을 등록할 수 있어요.",
            )
        cls = (
            db.query(ClassRoom)
            .filter(ClassRoom.organization_id == org_id, ClassRoom.name == label)
            .first()
        )
        if cls is None:
            new_grade = scope_grade if scope_grade is not None else target_grade
            cls = ClassRoom(organization_id=org_id, name=label, grade=new_grade, status="active")
            db.add(cls)
            db.flush()
        else:
            check_grade_scope(db, principal, org_id, _class_grade(cls))  # 기존 반이면 학년 범위 재확인
        resolved_class_id = cls.id
    codes = onboarding_service.generate_join_codes(
        db,
        organization_id=org_id,
        count=req.count,
        class_label=req.class_label,
        class_id=resolved_class_id,
        created_by=principal.id,
        names=req.names,
        genders=req.genders,
    )
    return {"ok": True, "issued": codes}


@router.post("/{org_id}/students/join-codes/{code_id}/reissue")
def reissue_student_join_code(
    org_id: str,
    code_id: str,
    principal: Principal = Depends(require_grade_head),
    db: Session = Depends(get_db),
):
    """미가입(미사용) 학생 가입 코드를 새 코드로 재발급 — 학생이 코드를 잊었을 때.
    옛 코드는 즉시 무효. 원문은 이 응답에서만 1회 노출. 학년부장은 담당 학년 학생만."""
    check_org_scope(principal, org_id)
    scope_grade = _scope_grade(db, principal)
    jc = db.get(StudentJoinCode, code_id)
    if jc is None or jc.organization_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="가입 코드를 찾을 수 없어요.")
    if jc.used_at is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="이미 가입에 사용된 코드예요. 재발급이 필요 없어요."
        )
    if scope_grade is not None:
        cls = db.get(ClassRoom, jc.class_id) if jc.class_id else None
        g = _class_grade(cls) if cls else (parse_grade(jc.class_label) if jc.class_label else None)
        if g != scope_grade:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="담당 학년 학생이 아니에요.")
    result = onboarding_service.reissue_join_code(db, jc)
    audit(
        db,
        action="org.student_code_reissue",
        actor_user_id=principal.id,
        organization_id=org_id,
        target_type="join_code",
        target_id=code_id,
    )
    db.commit()
    return {"ok": True, "issued": [result]}


@router.post("/{org_id}/students/{student_id}/invite-code")
def issue_invite(
    org_id: str,
    student_id: str,
    principal: Principal = Depends(require_grade_head),
    db: Session = Depends(get_db),
):
    """학생 1명 귀속 학부모 초대 코드 발급(고엔트로피·만료·2회 허용).
    교장=전체 학생, 학년부장=자기 담당 학년 학생만. (담임은 교사앱 /teacher 경로로 자기 반 학생)"""
    check_org_scope(principal, org_id)
    st = _student_in_org(db, org_id, student_id)  # 타 기관 학생 대상 발급 차단(크로스테넌트 IDOR)
    # 학년부장은 담당 학년 학생만 — 학생의 반 학년으로 판정(반 미배정이면 학년부장은 fail-closed)
    cls = db.get(ClassRoom, st.class_id) if st.class_id else None
    check_grade_scope(db, principal, org_id, _class_grade(cls) if cls else None)
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


def _active_homeroom(db: Session, cls: ClassRoom | None) -> User | None:
    """반의 '살아있는' 담임/보조 담임 사용자 — 없으면 None (교장 비상 초기화 가능 판단용).

    active 인 교사만 '실제로 초기화할 수 있는 담임'으로 본다. pending(가입 미완)·disabled
    교사는 로그인해 초기화할 수 없으므로 담임 없는 반과 같이 취급 → 교장 비상 초기화 허용.
    """
    if cls is None:
        return None
    for tid in (cls.teacher_id, cls.assistant_teacher_id):
        if not tid:
            continue
        u = db.get(User, tid)
        if u is not None and u.status == "active":
            return u
    return None


@router.post("/{org_id}/students/{student_id}/reset-password")
def reset_student_password(
    org_id: str,
    student_id: str,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """학생 비밀번호 초기화 — 원칙은 담임 교사, 교장(org_admin)은 '담임 없는 반'만 비상 초기화.

    평상시엔 담임이 `POST /teacher/class/students/{id}/reset-password`로 초기화한다.
    담임/보조 담임이 배정되지 않은(또는 비활성화된) 반의 학생은 아무도 초기화할 수
    없어 학생이 비번을 잊으면 갇힌다 → 교장이 비상 fallback으로만 초기화할 수 있다.
    담임이 살아있는 반이면 교장은 403(담임에게 요청). 모든 초기화는 감사에 남는다.
    """
    check_org_scope(principal, org_id)
    student = _student_in_org(db, org_id, student_id)
    if student.status == "disabled":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="학생을 찾을 수 없습니다.")
    cls = db.get(ClassRoom, student.class_id) if student.class_id else None
    homeroom = _active_homeroom(db, cls)
    if homeroom is not None:
        # 담임이 살아있는 반 — 교장은 초기화 불가, 담임에게 요청
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=f"이 학생은 담임({homeroom.name}) 선생님만 초기화할 수 있어요. 담당 선생님에게 요청해 주세요.",
        )
    # 담임 없는 반(미배정/결원) — 교장 비상 초기화
    temp = f"cat-{_secrets.randbelow(9000) + 1000}"
    student.password_hash = _hash_password(temp)
    student.must_change_password = True  # 첫 로그인 시 강제 변경 (전역 ForcePasswordGate)
    _auth_service.logout(db, student.id)  # 기존 세션 폐기 → 모든 기기 로그아웃
    audit(
        db,
        action="student.password_reset",
        actor_user_id=principal.id,
        organization_id=org_id,
        target_type="student",
        target_id=student.id,
        after={"by": "org_admin_fallback", "class_id": student.class_id},  # 담임 아닌 교장 비상 초기화 표시
    )
    db.commit()
    return {"ok": True, "temp_password": temp, "fallback": True}  # 임시 비번 1회 노출


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
    principal: Principal = Depends(require_grade_head),
    db: Session = Depends(get_db),
):
    """학생을 특정 반으로 배정/이동. 반이 없으면 만들어 연결. (반배정)

    학년부장은 자기 담당 학년의 반으로만 배정/생성할 수 있다.
    """
    check_org_scope(principal, org_id)
    scope_grade = _scope_grade(db, principal)  # None=교장(전 학년), 정수=학년부장
    st = _student_in_org(db, org_id, student_id)
    label = (req.class_label or "").strip()
    if not label:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="반 이름이 필요합니다.")
    target_grade = parse_grade(label)
    cls = (
        db.query(ClassRoom)
        .filter(ClassRoom.organization_id == org_id, ClassRoom.name == label)
        .first()
    )
    if cls is not None:
        # 기존 반: 그 반의 실제 학년으로 범위 검사 (grade=None이면 check_grade_scope가 거부 — fail-closed)
        check_grade_scope(db, principal, org_id, _class_grade(cls))
        if cls.status != "active":
            cls.status = "active"  # 해체됐던 반이면 되살려서 배정 (아카이브 반에 학생이 묻히지 않도록)
    else:
        # 새 반 생성 — 학년부장은 반 이름이 담당 학년으로 파싱돼야 함(fail-closed).
        if scope_grade is not None and target_grade != scope_grade:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail=f"{scope_grade}학년 담당이라 담당 학년 반(예: {scope_grade}-1반)만 만들 수 있어요.",
            )
        new_grade = target_grade if target_grade is not None else scope_grade
        cls = ClassRoom(organization_id=org_id, name=label, grade=new_grade, status="active")
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


# ---------------------------------------------------------------- 기관 API 키 관리 (교장 전용)
# 자기 기관 키만. 발급은 구매 범위(요금제 제품 + 구매 과목)로 강제되고, 외부 키라
# first_party=False로 발급 과목에 고정된다. 운영자(OPS) 콘솔과 권한·범위가 분리된다.
class _OrgIssueKeyReq(_BaseModel):
    product: str
    subject: str | None = None
    label: str | None = None
    domain: str | None = None


def _org_key_row(k: ApiKey, usage: int = 0) -> dict:
    from app.services import captcha_service as _cs

    return {
        "id": k.id,
        "product": k.product,
        "product_name": _cs.PRODUCTS.get(k.product, k.product),
        "subject": k.subject,
        "label": k.label,
        "first_party": k.first_party,
        "site_key": k.site_key,  # 공개키 — 노출 OK
        "status": k.status,
        "usage_month": usage,  # 이번 달 이 키의 challenge 발급 수
        "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
        "created_at": k.created_at.isoformat() if k.created_at else None,
    }


@router.get("/{org_id}/api-entitlements")
def org_api_entitlements(
    org_id: str,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """이 기관이 발급 가능한 제품·과목 + 이달 사용량/한도 — 발급 폼이 이 범위만 보여준다."""
    check_org_scope(principal, org_id)
    from app.services import captcha_service as _cs

    ent = _cs.org_entitlements(db, org_id)
    plan = _cs.plan_for_org(db, org_id)
    ent["usage"] = {
        "used": _cs._usage_this_month(db, org_id),
        "quota": plan.api_quota if plan else 0,
    }
    # 과목별 이번 달 호출 — 과목별 판매/사용량 대시보드용
    ent["subject_usage"] = aggregate.subject_usage_this_month(db, org_id)
    ent["product_names"] = _cs.PRODUCTS
    return ent


@router.get("/{org_id}/api-keys")
def org_api_keys(
    org_id: str,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """자기 기관 API 키 목록(secret 제외 — 공개 site_key만)."""
    check_org_scope(principal, org_id)
    rows = (
        db.query(ApiKey)
        .filter(ApiKey.organization_id == org_id, ApiKey.status != "deleted")
        .order_by(ApiKey.created_at.desc())
        .all()
    )
    usage = aggregate.key_usage_this_month(db, org_id)  # {api_key_id: 이달 호출수}
    return [_org_key_row(k, usage.get(k.id, 0)) for k in rows]


@router.post("/{org_id}/api-keys")
def org_issue_api_key(
    org_id: str,
    req: _OrgIssueKeyReq,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """기관 관리자 셀프 발급 — 구매 범위 내에서만. 외부 키(first_party=False)로 과목 고정.

    secret_key는 이 응답에서만 노출된다.
    """
    check_org_scope(principal, org_id)
    from app.services import captcha_service as _cs

    ent = _cs.org_entitlements(db, org_id)
    if req.product not in _cs.PRODUCTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="알 수 없는 제품입니다.")
    if req.product not in ent["products"]:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"현재 요금제로는 '{_cs.PRODUCTS[req.product]}'를 발급할 수 없어요. 운영자에게 문의해 주세요.",
        )
    if req.product == "edu":
        if req.subject not in _cs.EDU_SUBJECTS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="교육형은 과목을 지정해야 합니다.")
        if req.subject not in ent["edu_subjects"]:
            raise HTTPException(
                status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"'{req.subject}' 과목은 아직 구매하지 않았어요. 운영자에게 문의해 주세요.",
            )
    issued = _cs.issue_key(
        db, org_id=org_id, product=req.product, subject=req.subject,
        label=req.label, domain=req.domain, created_by=principal.id, first_party=False,
    )
    audit(
        db, action="captcha.api_key_issue", actor_user_id=principal.id,
        organization_id=org_id, target_type="api_key", target_id=issued["id"],
        after={"product": req.product, "subject": req.subject, "label": req.label, "by": "org"},
    )
    db.commit()
    return {"ok": True, **issued}


@router.delete("/{org_id}/api-keys/{key_id}")
def org_revoke_api_key(
    org_id: str,
    key_id: str,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """자기 기관 키만 비활성(soft disable)."""
    check_org_scope(principal, org_id)
    k = db.get(ApiKey, key_id)
    if k is None or k.organization_id != org_id or k.status == "deleted":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="키를 찾을 수 없습니다.")
    k.status = "disabled"
    audit(
        db, action="captcha.api_key_revoke", actor_user_id=principal.id,
        organization_id=org_id, target_type="api_key", target_id=k.id,
    )
    db.commit()
    return {"ok": True}


@router.post("/{org_id}/api-keys/{key_id}/rotate-secret")
def org_rotate_secret(
    org_id: str,
    key_id: str,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """secret_key 재발급 — 유출 대응. site_key는 그대로라 위젯 재배포 불필요. secret 1회 노출."""
    check_org_scope(principal, org_id)
    from app.services import captcha_service as _cs

    k = db.get(ApiKey, key_id)
    if k is None or k.organization_id != org_id or k.status == "deleted":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="키를 찾을 수 없습니다.")
    secret = _cs.rotate_secret(db, k)
    audit(
        db, action="captcha.api_key_rotate", actor_user_id=principal.id,
        organization_id=org_id, target_type="api_key", target_id=k.id,
    )
    db.commit()
    return {"ok": True, "site_key": k.site_key, "secret_key": secret}


# ---------------------------------------------------------------- 기관 활동 기록 (자기 기관 스코프)
@router.get("/{org_id}/audit-logs")
def org_audit_logs(
    org_id: str,
    action: str | None = None,
    date_from: str | None = None,  # 'YYYY-MM-DD' (해당일 00:00 포함)
    date_to: str | None = None,  # 'YYYY-MM-DD' (해당일 끝까지 포함)
    page: int = 1,
    page_size: int = 50,
    principal: Principal = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """기관 활동 기록 — 교장/기관 관리자가 자기 기관 것만 본다.

    운영 감사로그(GET /ops/logs)와 달리:
    - 자기 organization_id로 스코프 고정 (타 기관 조회 불가)
    - 운영자(ops) 내부 행위는 제외 — 기관 화면엔 기관 구성원(관리자·교사·학부모·학생)의
      행동만 보인다. 요금제 변경·비번 재발급 같은 운영 조치는 운영 콘솔 몫.
    - 학생 실행자는 ops 콘솔과 동일 규칙의 익명 코드("학생 XXXXXX")로만 표시.
    """
    import hashlib as _hashlib
    from datetime import date as _date, datetime as _dt, time as _time, timedelta as _td

    from sqlalchemy import or_ as _or

    from app.core.config import get_settings as _get_settings
    from app.models import AuditLog

    check_org_scope(principal, org_id)

    page = max(1, page)
    page_size = max(1, min(200, page_size))

    q = db.query(AuditLog).filter(AuditLog.organization_id == org_id)
    if action:
        q = q.filter(AuditLog.action == action)
    try:
        if date_from:
            q = q.filter(AuditLog.created_at >= _dt.combine(_date.fromisoformat(date_from), _time.min))
        if date_to:
            q = q.filter(AuditLog.created_at < _dt.combine(_date.fromisoformat(date_to) + _td(days=1), _time.min))
    except ValueError:
        pass

    # 운영자 내부 행위 제외 — actor가 ops 역할인 로그는 기관에 노출하지 않는다.
    # (actor 없음/학생 actor는 User에 없으므로 유지된다)
    ops_ids = [uid for (uid,) in db.query(User.id).filter(User.role == "ops").all()]
    if ops_ids:
        q = q.filter(_or(AuditLog.actor_user_id.is_(None), AuditLog.actor_user_id.notin_(ops_ids)))

    total = q.count()
    rows = (
        q.order_by(AuditLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    actor_ids = {log.actor_user_id for log in rows if log.actor_user_id}
    users = {u.id: u for u in db.query(User).filter(User.id.in_(list(actor_ids) or [""]))}
    students = {
        s.id: s
        for s in db.query(StudentProfile).filter(StudentProfile.id.in_(list(actor_ids) or [""]))
    }
    _salt = _get_settings().JWT_SECRET_KEY

    def _actor(log) -> str | None:
        aid = log.actor_user_id
        if not aid:
            return None
        u = users.get(aid)
        if u is not None:
            role = {
                "org_admin": "기관 관리자", "grade_head": "학년부장",
                "teacher": "교사", "parent": "학부모",
            }.get(u.role, u.role)
            return f"{u.name} ({role})"
        s = students.get(aid)
        if s is not None:
            # ops 콘솔과 동일 salt·규칙 — 두 콘솔에서 같은 학생이 같은 코드로 보인다.
            code = _hashlib.sha256(f"{_salt}:{s.id}".encode()).hexdigest()[:6].upper()
            return f"학생 {code}"
        return None

    items = [
        {
            "id": log.id,
            "action": log.action,
            "actor_name": _actor(log),
            "actor_email": (users[log.actor_user_id].email if log.actor_user_id in users else None),
            "target_type": log.target_type,
            "target_id": log.target_id,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in rows
    ]

    # 필터 선택지 — 자기 기관에 실제 존재하는 action만 (ops 행위 제외 조건 동일 적용)
    facet_q = db.query(AuditLog.action).filter(AuditLog.organization_id == org_id)
    if ops_ids:
        facet_q = facet_q.filter(
            _or(AuditLog.actor_user_id.is_(None), AuditLog.actor_user_id.notin_(ops_ids))
        )
    action_facet = sorted(a for (a,) in facet_q.distinct().all() if a)

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "actions": action_facet,
    }
