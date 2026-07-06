"""교사 API — 담당 학급 범위만 (require_teacher)."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.permissions import Principal, require_teacher
from app.db.session import get_db
from app.models import (
    ClassRoom,
    FamilyMessage,
    LearningSummary,
    Membership,
    Organization,
    ParentStudentLink,
    StudentProfile,
    User,
)
from app.schemas.teacher import (
    AddStudentByCode,
    ClassStudentUpdate,
    FamilyMessageCreate,
    TeacherProfileUpdate,
)
from app.services import aggregate
from app.services.aggregate import fb
from app.services.stats import D  # DB(stat_blobs) 우선, design_data fallback
from app.utils.helpers import audit, status_key, status_label

router = APIRouter(prefix="/teacher", tags=["teacher"])


def _my_class(db: Session, principal: Principal) -> ClassRoom:
    cls = (
        db.query(ClassRoom)
        .filter(ClassRoom.teacher_id == principal.id, ClassRoom.status == "active")
        .order_by(ClassRoom.name)
        .first()
    )
    if cls is None and principal.role == "org_admin":
        cls = (
            db.query(ClassRoom)
            .filter(
                ClassRoom.organization_id == principal.organization_id,
                ClassRoom.status == "active",
            )
            .order_by(ClassRoom.name)
            .first()
        )
    if cls is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="담당 학급이 없습니다.")
    return cls


def _week_summaries(db: Session, org_id: str) -> dict[str, LearningSummary]:
    rows = (
        db.query(LearningSummary)
        .filter(
            LearningSummary.organization_id == org_id,
            LearningSummary.period_type == "week",
        )
        .all()
    )
    return {r.student_id: r for r in rows}


def _acc(summary: LearningSummary | None) -> int:
    if summary is None or not summary.total_count:
        return 0
    return round(summary.correct_count / summary.total_count * 100)


def _display_name(s: StudentProfile) -> str:
    """교사/기관 화면 표시용 이름.

    실명 컬럼이 없어 디자인의 '성 포함 표기' 매핑을 쓰되, 닉네임이 바뀌면
    (매핑과 어긋나면) DB 닉네임을 우선한다 → 이름 변경이 화면에 반영된다.
    """
    full = D.CODE_FULL_NAME.get(s.student_code)
    if full and s.nickname and s.nickname in full:
        return full
    return s.nickname


def _student_row(s: StudentProfile, summary: LearningSummary | None) -> dict:
    detail = (summary.detail if summary else {}) or {}
    return {
        "id": s.id,
        "name": _display_name(s),
        "nickname": s.nickname,
        "age": s.age,
        "code": s.student_code,
        "today": detail.get("today", "none"),
        "acc": _acc(summary),
        "streak": summary.streak_days if summary else 0,
        "status": status_label(s.status),
        "solved": summary.total_count if summary else 0,
    }


# ---------------------------------------------------------------- 대시보드
@router.get("/dashboard")
def dashboard(principal: Principal = Depends(require_teacher), db: Session = Depends(get_db)):
    cls = _my_class(db, principal)
    students = (
        db.query(StudentProfile)
        .filter(StudentProfile.class_id == cls.id, StudentProfile.status != "disabled")
        .all()
    )
    d = dict(D.TEACHER_DASHBOARD)  # 문구/할일 등 텍스트성 값은 D 유지
    # KPI/차트: learning_attempts 실집계 — 학급 시도가 전혀 없으면 D 유지
    agg = aggregate.teacher_dashboard(db, students) or {}
    kpis = {**d.get("kpis", {}), **agg.get("kpis", {}), "total_students": len(students)}
    return {
        **d,
        "teacher_name": principal.user.name if principal.user else "",
        "class_id": cls.id,
        "class_name": cls.name,  # 실테이블(classes) — D의 class_name을 덮어쓴다
        "kpis": kpis,
        "bar_data": fb(agg.get("bar_data"), d.get("bar_data")),
        "game_bars": fb(agg.get("game_bars"), d.get("game_bars")),
        "attention": fb(agg.get("attention"), d.get("attention")),
    }


# ---------------------------------------------------------------- 우리반
@router.get("/class/students")
def my_class_students(
    principal: Principal = Depends(require_teacher), db: Session = Depends(get_db)
):
    # 담당 학급이 아직 없는 교사(신규 등)는 에러 대신 빈 학급으로 응답 → 화면이 깨지지 않음
    try:
        cls = _my_class(db, principal)
    except HTTPException:
        return {
            "class_id": None,
            "class_name": "담당 학급 없음",
            "total": 0,
            "students": [],
            "directory_codes": [d["code"] for d in D.CLASS_DIRECTORY],
        }
    summaries = _week_summaries(db, cls.organization_id)
    students = (
        db.query(StudentProfile)
        .filter(StudentProfile.class_id == cls.id, StudentProfile.status != "disabled")
        .order_by(StudentProfile.student_login_id)
        .all()
    )
    return {
        "class_id": cls.id,
        "class_name": cls.name,
        "total": len(students),
        "students": [_student_row(s, summaries.get(s.id)) for s in students],
        "directory_codes": [d["code"] for d in D.CLASS_DIRECTORY],
    }


@router.post("/class/students")
def add_student_by_code(
    req: AddStudentByCode,
    principal: Principal = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    cls = _my_class(db, principal)
    code = req.student_code.strip().upper()
    student = (
        db.query(StudentProfile)
        .filter(
            StudentProfile.student_code == code,
            StudentProfile.organization_id == cls.organization_id,
        )
        .first()
    )
    if student is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="학생 코드를 찾을 수 없습니다.")
    if student.class_id == cls.id:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="이미 우리 반 학생입니다.")
    # 이미 다른 학급에 배정된 학생을 코드 추측만으로 빼오지 못하게 차단 —
    # 기존 학급에서 먼저 제외해야 재배정 가능 (학급 간 학생 탈취 방지)
    if student.class_id is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="이미 다른 반에 배정된 학생이에요. 기존 반에서 먼저 제외해야 해요.",
        )
    before = {"class_id": student.class_id}
    student.class_id = cls.id
    audit(
        db,
        action="teacher.class_student_add",
        actor_user_id=principal.id,
        organization_id=cls.organization_id,
        target_type="student_profile",
        target_id=student.id,
        before=before,
        after={"class_id": cls.id, "student_code": code},
    )
    db.commit()
    summaries = _week_summaries(db, cls.organization_id)
    return {"ok": True, "student": _student_row(student, summaries.get(student.id))}


def _get_class_student(db: Session, principal: Principal, student_id: str) -> tuple[ClassRoom, StudentProfile]:
    cls = _my_class(db, principal)
    student = db.get(StudentProfile, student_id)
    if student is None or student.class_id != cls.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="우리 반 학생이 아닙니다.")
    return cls, student


@router.get("/class/students/{student_id}")
def class_student_detail(
    student_id: str,
    principal: Principal = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    cls, student = _get_class_student(db, principal, student_id)
    summaries = _week_summaries(db, cls.organization_id)
    row = _student_row(student, summaries.get(student.id))
    acc = row["acc"]
    clamp = lambda v: max(35, min(99, v))  # noqa: E731
    skills = [
        {"label": "한글 낱말", "pct": clamp(acc + 4)},
        {"label": "그림 찾기", "pct": clamp(acc + 2)},
        {"label": "끌어놓기", "pct": clamp(acc - 8)},
        {"label": "숫자 놀이터", "pct": clamp(acc - 14)},
    ]
    return {
        **row,
        "skills": skills,
        "comment": D.MY_CLASS_COMMENTS.get(student.student_code, D.MY_CLASS_COMMENT_DEFAULT),
    }


@router.patch("/class/students/{student_id}")
def update_class_student(
    student_id: str,
    req: ClassStudentUpdate,
    principal: Principal = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    cls, student = _get_class_student(db, principal, student_id)
    before = {"nickname": student.nickname, "age": student.age, "status": student.status}
    if req.nickname is not None and req.nickname.strip():
        student.nickname = req.nickname.strip()[:10]
    if req.age is not None:
        student.age = req.age
    if req.status is not None:
        student.status = status_key(req.status)
    audit(
        db,
        action="teacher.class_student_update",
        actor_user_id=principal.id,
        organization_id=cls.organization_id,
        target_type="student_profile",
        target_id=student.id,
        before=before,
        after={"nickname": student.nickname, "age": student.age, "status": student.status},
    )
    db.commit()
    summaries = _week_summaries(db, cls.organization_id)
    return {"ok": True, "student": _student_row(student, summaries.get(student.id))}


@router.delete("/class/students/{student_id}")
def remove_class_student(
    student_id: str,
    principal: Principal = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    cls, student = _get_class_student(db, principal, student_id)
    student.class_id = None
    audit(
        db,
        action="teacher.class_student_remove",
        actor_user_id=principal.id,
        organization_id=cls.organization_id,
        target_type="student_profile",
        target_id=student.id,
        before={"class_id": cls.id},
        after={"class_id": None},
    )
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- 전교 roster
@router.get("/students")
def all_students(
    grade: int | None = Query(default=None),
    cls: int | None = Query(default=None),
    q: str | None = Query(default=None),
    principal: Principal = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    org_id = principal.organization_id
    rows = (
        db.query(LearningSummary)
        .filter(
            LearningSummary.organization_id == org_id,
            LearningSummary.period_type == "week",
        )
        .all()
    )
    roster_meta = {r.student_id: (r.detail or {}) for r in rows if (r.detail or {}).get("roster")}
    students = (
        db.query(StudentProfile).filter(StudentProfile.id.in_(list(roster_meta.keys()))).all()
        if roster_meta
        else []
    )
    summaries = {r.student_id: r for r in rows}

    groups: dict[str, list[dict]] = {}
    for s in students:
        meta = roster_meta[s.id]
        g, c = meta.get("g"), meta.get("c")
        if grade is not None and g != grade:
            continue
        if cls is not None and c != cls:
            continue
        if q and q.strip() and q.strip() not in s.nickname:
            continue
        key = f"{g}-{c}"
        groups.setdefault(key, []).append(
            {
                "id": s.id,
                "name": s.nickname,
                "acc": _acc(summaries.get(s.id)),
                "sessions": meta.get("sessions", ""),
                "weak": meta.get("weak", ""),
                "status": status_label(s.status),
            }
        )
    # 담당 교사: 실테이블(classes.teacher_id → users) 우선, 없으면 디자인 매핑
    teacher_by_key: dict[str, str] = {}
    class_rows = (
        db.query(ClassRoom, User)
        .outerjoin(User, User.id == ClassRoom.teacher_id)
        .filter(ClassRoom.organization_id == org_id, ClassRoom.status == "active")
        .all()
    )
    for c_row, u in class_rows:
        if u is not None:
            teacher_by_key[c_row.name.replace("반", "")] = u.name

    out = []
    for key in sorted(groups):
        g, c = key.split("-")
        out.append(
            {
                "label": f"{g}학년 {c}반",
                "badge": key,
                "teacher": teacher_by_key.get(key) or D.ROSTER_TEACHERS.get(key, "미배정"),
                "count": len(groups[key]),
                "students": sorted(groups[key], key=lambda x: x["name"]),
            }
        )
    total = sum(g["count"] for g in out)
    return {"total": len(roster_meta), "filtered": total, "groups": out}


# ---------------------------------------------------------------- 학습 분석
@router.get("/analytics")
def analytics(
    period: str = Query(default="week"),
    subject: str | None = Query(default=None),
    principal: Principal = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    p = period if period in D.TEACHER_ANALYTICS else "week"
    d = dict(D.TEACHER_ANALYTICS[p])
    ts = subject if subject in D.TEACHER_ANALYTICS_SUBJ_LAST else None

    # D fallback 시리즈 (디자인 로직 그대로)
    acc = list(d["accPct"])
    if ts:
        shift = D.TEACHER_ANALYTICS_SUBJ_LAST[ts] - acc[-1]
        acc = [max(45, min(99, v + shift)) for v in acc]
    d_subjects = [
        {**s, "correct": round(s["total"] * s["pct"] / 100)} for s in D.TEACHER_ANALYTICS_SUBJECTS
    ]

    # learning_attempts 실집계 — 학급 시도 없으면 D 유지 (축 라벨은 D 축 길이에 맞춤)
    cls = _my_class(db, principal)
    students = (
        db.query(StudentProfile)
        .filter(StudentProfile.class_id == cls.id, StudentProfile.status != "disabled")
        .all()
    )
    agg = aggregate.analytics(db, students, p, len(d["axis"]), ts) or {}

    return {
        "period": p,
        "subject": ts or "all",
        "class_name": cls.name,  # 실테이블(classes) — 페이지 타이틀용
        **{k: v for k, v in d.items() if k != "accPct"},
        **{k: agg[k] for k in ("kAcc", "kAccDelta", "kActive", "kSolved", "kHelp") if k in agg},
        "accSeries": fb(agg.get("accSeries"), acc),
        "avg": fb(agg.get("avg"), round(sum(acc) / len(acc))),
        "subjects": fb(agg.get("subjects"), d_subjects),
        "reasons": fb(agg.get("reasons"), D.TEACHER_ANALYTICS_REASONS),
        "attention": fb(agg.get("attention"), D.TEACHER_ANALYTICS_ATTENTION),
        "students": fb(agg.get("students"), D.TEACHER_ANALYTICS_STUDENTS),
        "subjTarget": "80%",
        "ai_summary": D.TEACHER_ANALYTICS_AI,  # AI 분석 요약 (stat_blobs 수정 가능)
        "insight": D.TEACHER_ANALYTICS_INSIGHT,  # 사이드바 인사이트 문구
    }


# ---------------------------------------------------------------- 가정안내
@router.get("/family-messages")
def family_messages(
    principal: Principal = Depends(require_teacher), db: Session = Depends(get_db)
):
    cls = _my_class(db, principal)
    students = (
        db.query(StudentProfile)
        .filter(StudentProfile.class_id == cls.id, StudentProfile.status != "disabled")
        .order_by(StudentProfile.student_login_id)
        .all()
    )
    link_rows = (
        db.query(ParentStudentLink)
        .filter(
            ParentStudentLink.student_id.in_([s.id for s in students] or [""]),
            ParentStudentLink.status == "approved",
        )
        .all()
    )
    linked_ids = {l.student_id for l in link_rows}
    student_rows = []
    for s in students:
        full_name = _display_name(s)
        design = D.FAMILY_PARENTS.get(full_name, {})
        student_rows.append(
            {
                "id": s.id,
                "name": full_name,
                "parent": design.get("parent", "보호자"),
                "linked": bool(design.get("linked", s.id in linked_ids)) or s.id in linked_ids,
            }
        )
    sent_rows = (
        db.query(FamilyMessage)
        .filter(FamilyMessage.teacher_id == principal.id)
        .order_by(FamilyMessage.created_at.desc())
        .limit(20)
        .all()
    )
    students_by_id = {s.id: s for s in students}
    sent = []
    for m in sent_rows:
        target = students_by_id.get(m.student_id) or db.get(StudentProfile, m.student_id)
        full_name = _display_name(target) if target else ""
        design = D.FAMILY_PARENTS.get(full_name, {}) if target else {}
        sent.append(
            {
                "id": m.id,
                "recipient": design.get("parent", "보호자"),
                "student_name": full_name,
                "body": m.message,
                "status": m.status,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
        )
    return {"students": student_rows, "sent": sent}


@router.post("/family-messages")
def send_family_message(
    req: FamilyMessageCreate,
    principal: Principal = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    cls = _my_class(db, principal)
    created = []
    for sid in req.student_ids:
        student = db.get(StudentProfile, sid)
        if student is None or student.class_id != cls.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="우리 반 학생이 아닙니다.")
        msg = FamilyMessage(
            organization_id=cls.organization_id,
            teacher_id=principal.id,
            student_id=sid,
            message=req.message.strip(),
            status="sent",
        )
        db.add(msg)
        created.append(msg)
    db.commit()
    return {"ok": True, "sent": len(created), "ids": [m.id for m in created]}


# ---------------------------------------------------------------- 마이페이지
@router.get("/profile")
def profile(principal: Principal = Depends(require_teacher), db: Session = Depends(get_db)):
    user = principal.user
    org = db.get(Organization, principal.organization_id) if principal.organization_id else None
    membership = (
        db.query(Membership)
        .filter(Membership.user_id == principal.id, Membership.role == "teacher")
        .first()
    )
    homeroom = (
        db.query(ClassRoom)
        .filter(ClassRoom.teacher_id == principal.id, ClassRoom.status == "active")
        .order_by(ClassRoom.name)
        .first()
    )
    code_remain = None
    if org and org.code_expires_at:
        code_remain = max(0, (org.code_expires_at - datetime.utcnow()).days)
    return {
        "name": user.name if user else "",
        "email": user.email if user else "",
        "phone": user.phone if user else "",
        "role": (membership.position if membership and membership.position else "담임 교사"),
        "career_years": membership.career_years if membership else None,
        "class_name": homeroom.name if homeroom else None,
        "org_name": org.name if org else None,
        "org_code": org.code if org else None,
        "teacher_code": membership.teacher_code if membership else None,
        "code_expires_at": org.code_expires_at.isoformat() if org and org.code_expires_at else None,
        "code_remain_days": code_remain,
    }


@router.patch("/profile")
def save_profile(
    req: TeacherProfileUpdate,
    principal: Principal = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    user = principal.user
    before = {"name": user.name, "phone": user.phone}
    if req.name is not None and req.name.strip():
        user.name = req.name.strip()
    if req.phone is not None:
        user.phone = req.phone
    if req.position is not None:
        membership = (
            db.query(Membership)
            .filter(Membership.user_id == principal.id, Membership.role == "teacher")
            .first()
        )
        if membership:
            membership.position = req.position
    audit(
        db,
        action="teacher.profile_update",
        actor_user_id=principal.id,
        organization_id=principal.organization_id,
        target_type="user",
        target_id=principal.id,
        before=before,
        after={"name": user.name, "phone": user.phone},
    )
    db.commit()
    return {"ok": True}


@router.get("/classes")
def my_classes(principal: Principal = Depends(require_teacher), db: Session = Depends(get_db)):
    rows = (
        db.query(ClassRoom)
        .filter(ClassRoom.teacher_id == principal.id, ClassRoom.status == "active")
        .order_by(ClassRoom.name)
        .all()
    )
    out = []
    for i, c in enumerate(rows):
        count = (
            db.query(StudentProfile)
            .filter(StudentProfile.class_id == c.id, StudentProfile.status != "disabled")
            .count()
        )
        design = {"1-2반": ("담임", "학생 22명 · 숫자·한글 학습"), "1-3반": ("수학 전담", "학생 24명 · 숫자 놀이터")}
        role, caption = design.get(c.name, ("담임" if i == 0 else "교과", f"학생 {count}명"))
        out.append(
            {
                "id": c.id,
                "name": c.name,
                "role": role,
                "caption": caption,
                "student_count": count,
            }
        )
    return out
