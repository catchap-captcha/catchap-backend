"""교사 API — 담당 학급 범위만 (require_teacher)."""

import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.permissions import Principal, require_teacher
from app.core.security import hash_password
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
from app.services import aggregate, auth_service
from app.services.aggregate import fb
from app.services.stats import D  # DB(stat_blobs) 우선, design_data fallback
from app.utils.helpers import audit, status_key, status_label, student_display_name, summary_acc, utc_to_local

router = APIRouter(prefix="/teacher", tags=["teacher"])


def _my_class(db: Session, principal: Principal) -> ClassRoom:
    # 담임(teacher_id) 우선, 없으면 보조 담임(assistant_teacher_id)으로 배정된 반 — 담임 결원 대체
    cls = (
        db.query(ClassRoom)
        .filter(ClassRoom.teacher_id == principal.id, ClassRoom.status == "active")
        .order_by(ClassRoom.name)
        .first()
    )
    if cls is None:
        cls = (
            db.query(ClassRoom)
            .filter(
                ClassRoom.assistant_teacher_id == principal.id,
                ClassRoom.status == "active",
            )
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
    return summary_acc(summary)


def _display_name(s: StudentProfile) -> str:
    """교사 화면 표시 이름 — 공용 로직(helpers.student_display_name) 사용."""
    return student_display_name(s, D.CODE_FULL_NAME)


def _student_row(
    s: StudentProfile, summary: LearningSummary | None, real: dict | None = None
) -> dict:
    """학생 1명 명단 행. real(learning_attempts 실집계)이 있으면 실데이터,
    없으면(미플레이/데모) seed LearningSummary 값으로 폴백한다."""
    detail = (summary.detail if summary else {}) or {}
    if real is not None:
        today, acc, streak, solved = real["today"], real["acc"], real["streak"], real["solved"]
    else:
        today = detail.get("today", "none")
        acc = _acc(summary)
        streak = summary.streak_days if summary else 0
        solved = summary.total_count if summary else 0
    return {
        "id": s.id,
        "name": _display_name(s),
        "nickname": s.nickname,
        "age": s.age,
        "gender": s.gender,
        "code": s.student_code,
        "today": today,
        "acc": acc,
        "streak": streak,
        "status": status_label(s.status),
        "solved": solved,
        # 실집계(real)가 없어 seed 데모값으로 표시된 행 = '데모칸'. 프론트가 '데모' 라벨.
        "demo": real is None,
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
        # 학급에 실 시도가 없어 그래프·KPI가 전부 디자인(데모)값이면 demo=True
        "demo": not agg,
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
    # learning_attempts 실집계 — 실제 푸는 학생은 실데이터, 미플레이/데모는 seed 폴백
    real = aggregate.student_roster_metrics(db, [s.id for s in students])
    return {
        "class_id": cls.id,
        "class_name": cls.name,
        "total": len(students),
        "students": [_student_row(s, summaries.get(s.id), real.get(s.id)) for s in students],
        "directory_codes": [d["code"] for d in D.CLASS_DIRECTORY],
    }


@router.post("/class/students/{student_id}/reset-password")
def reset_class_student_password(
    student_id: str,
    principal: Principal = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """담임 교사가 자기 반 학생의 비밀번호를 초기화 — 임시 비번 발급 + 기존 세션 폐기 + 강제 변경.

    담당 반(담임/보조 담임) 학생만 초기화할 수 있고, 교장(org_admin)은 할 수 없다.
    학생은 다음 로그인 시 강제로 새 비밀번호를 정하게 된다(must_change_password).
    """
    if principal.role == "org_admin":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="학생 비밀번호 초기화는 담임 교사만 할 수 있어요."
        )
    cls = _my_class(db, principal)  # 담임(teacher_id)/보조(assistant_teacher_id)로 배정된 반
    student = db.get(StudentProfile, student_id)
    if (
        student is None
        or student.class_id != cls.id
        or student.status == "disabled"
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="담당 반 학생이 아니에요.")
    temp = f"cat-{secrets.randbelow(9000) + 1000}"
    student.password_hash = hash_password(temp)
    student.must_change_password = True  # 첫 로그인 시 새 비번 설정 강제 (전역 ForcePasswordGate)
    auth_service.logout(db, student.id)  # 기존 refresh 토큰 폐기 → 모든 기기 로그아웃
    audit(
        db,
        action="student.password_reset",
        actor_user_id=principal.id,
        organization_id=cls.organization_id,
        target_type="student",
        target_id=student.id,
    )
    db.commit()
    return {"ok": True, "temp_password": temp}  # 임시 비번은 1회 노출


@router.post("/class/students/{student_id}/invite-code")
def issue_class_parent_invite(
    student_id: str,
    principal: Principal = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """담임/보조 교사가 자기 반 학생의 학부모 초대 코드를 발급(고엔트로피·만료·2회 허용).
    자기 담당 반 학생만 — 다른 반 학생은 403. 코드 원문은 이 응답에서만 1회 노출."""
    from app.services import onboarding_service

    cls = _my_class(db, principal)  # 담임(teacher_id)/보조(assistant_teacher_id) 배정 반
    student = db.get(StudentProfile, student_id)
    if student is None or student.class_id != cls.id or student.status == "disabled":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="담당 반 학생이 아니에요.")
    code = onboarding_service.issue_parent_invite(
        db, student_id=student_id, organization_id=cls.organization_id, created_by=principal.id
    )
    audit(
        db,
        action="student.parent_invite",
        actor_user_id=principal.id,
        organization_id=cls.organization_id,
        target_type="student",
        target_id=student.id,
    )
    db.commit()
    return {"ok": True, "invite_code": code}


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
    before = {"nickname": student.nickname, "real_name": student.real_name, "age": student.age, "gender": student.gender, "status": student.status}
    if req.nickname is not None and req.nickname.strip():
        student.nickname = req.nickname.strip()[:10]
    if req.real_name is not None and req.real_name.strip():
        student.real_name = req.real_name.strip()[:100]
    if req.age is not None:
        student.age = req.age
    if req.gender is not None:
        student.gender = req.gender or None  # 빈문자면 미입력으로
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
        after={"nickname": student.nickname, "age": student.age, "gender": student.gender, "status": student.status},
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
def _class_gc(c: ClassRoom) -> tuple[int | None, int | None]:
    """ClassRoom → (학년, 반번호). grade 컬럼 우선, 반번호는 이름('1-2반')에서 파싱."""
    parts = (c.name or "").replace("반", "").split("-")
    g = c.grade if c.grade is not None else (int(parts[0]) if parts and parts[0].isdigit() else None)
    cn = None
    if len(parts) > 1 and parts[1].isdigit():
        cn = int(parts[1])
    elif parts and parts[0].isdigit():
        cn = int(parts[0])
    return g, cn


@router.get("/students")
def all_students(
    grade: int | None = Query(default=None),
    cls: int | None = Query(default=None),
    q: str | None = Query(default=None),
    principal: Principal = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    org_id = principal.organization_id
    # 실배정 학생 전체 (student_profiles/classes 실테이블 기준) — seed roster 폐기
    students = (
        db.query(StudentProfile)
        .filter(
            StudentProfile.organization_id == org_id,
            StudentProfile.class_id.isnot(None),
            StudentProfile.status != "disabled",
        )
        .all()
    )
    classes = {c.id: c for c in db.query(ClassRoom).filter(ClassRoom.organization_id == org_id).all()}
    real = aggregate.student_roster_metrics(db, [s.id for s in students])

    groups: dict[str, list[dict]] = {}
    for s in students:
        c = classes.get(s.class_id)
        if c is None:
            continue
        g, cn = _class_gc(c)
        if grade is not None and g != grade:
            continue
        if cls is not None and cn != cls:
            continue
        if q and q.strip() and q.strip() not in (s.nickname or "") and q.strip() not in (s.real_name or ""):
            continue
        m = real.get(s.id)
        groups.setdefault(f"{g}-{cn}", []).append(
            {
                "id": s.id,
                "name": _display_name(s),
                "code": s.student_code,
                "acc": m["acc"] if m else 0,
                "sessions": m["solved"] if m else 0,
                "week_min": m.get("week_min", 0) if m else 0,  # 이번 주 학습 시간(분)
                "weak": "",
                "status": status_label(s.status),
                # 실플레이 없는 학생 = 데모칸(정답률 0·미활동) — 프론트가 '데모' 라벨
                "demo": m is None,
            }
        )
    # 담당 교사 (classes.teacher_id → users) — 학급 수만큼 개별 조회하지 않게 배치 로드
    active_tids = {
        c.teacher_id for c in classes.values() if c.status == "active" and c.teacher_id
    }
    users_by_id = {
        u.id: u
        for u in db.query(User).filter(User.id.in_(active_tids or [""])).all()
    }
    teacher_by_key: dict[str, str] = {}
    for c in classes.values():
        if c.status != "active" or not c.teacher_id:
            continue
        g, cn = _class_gc(c)
        u = users_by_id.get(c.teacher_id)
        if u is not None:
            teacher_by_key[f"{g}-{cn}"] = u.name

    out = []
    for key in sorted(groups):
        g, cn = key.split("-")
        out.append(
            {
                "label": f"{g}학년 {cn}반",
                "badge": key,
                "teacher": teacher_by_key.get(key, "미배정"),
                "count": len(groups[key]),
                "students": sorted(groups[key], key=lambda x: x["name"]),
            }
        )
    total = sum(g["count"] for g in out)
    return {"total": len(students), "filtered": total, "groups": out}


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
        # 실 시도가 없어 시리즈·수치가 전부 디자인(데모)값이면 demo=True (문구는 항상 디자인)
        "demo": not agg,
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
    # 반을 옮긴 수신자 등 명단 밖 학생도 개별 조회 대신 한 번에 배치 로드
    missing_ids = {m.student_id for m in sent_rows if m.student_id not in students_by_id}
    if missing_ids:
        students_by_id.update(
            {
                s.id: s
                for s in db.query(StudentProfile)
                .filter(StudentProfile.id.in_(missing_ids))
                .all()
            }
        )
    sent = []
    for m in sent_rows:
        target = students_by_id.get(m.student_id)
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
    # 수신 학생을 한 번에 로드해 검증 — 다건 발송 시 학생 수만큼 조회하지 않는다
    students_by_id = {
        s.id: s
        for s in db.query(StudentProfile)
        .filter(StudentProfile.id.in_(req.student_ids or [""]))
        .all()
    }
    created = []
    for sid in req.student_ids:
        student = students_by_id.get(sid)
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
        # 만료는 UTC 저장 — 노출은 KST 벽시계로 변환
        "code_expires_at": (
            utc_to_local(org.code_expires_at).isoformat() if org and org.code_expires_at else None
        ),
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
    # 학급별 학생 수 — 반마다 COUNT 하지 않고 GROUP BY 한 번으로 집계
    counts = dict(
        db.query(StudentProfile.class_id, func.count(StudentProfile.id))
        .filter(
            StudentProfile.class_id.in_([c.id for c in rows] or [""]),
            StudentProfile.status != "disabled",
        )
        .group_by(StudentProfile.class_id)
        .all()
    )
    out = []
    for i, c in enumerate(rows):
        count = int(counts.get(c.id, 0))
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
