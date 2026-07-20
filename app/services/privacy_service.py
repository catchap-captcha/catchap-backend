"""개인정보 파기(익명화) — PIPA '목적 달성 후 지체없는 파기'. (사용자 결정 2026-07-13: 익명화)

식별 개인정보(실명·나이·성별·로그인ID·별명 등)만 파기/마스킹하고, 학습기록·행동데이터는
student_id 키로 익명 집계에 남긴다(서비스 지표·연구용 유지, 재식별 불가). 계정은
status="disabled"로 두어 기존 로그인/접근 차단을 그대로 재사용한다. 이미 익명화된 계정은
real_name이 None이므로 멱등하게 건너뛴다.
"""
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import StudentProfile, User


def anonymize_student(db: Session, student: StudentProfile) -> bool:
    """학생의 식별 PII를 파기(익명화)한다. 이미 익명화됐으면 False, 파기했으면 True.

    커밋은 호출자 책임. 로그인은 status='disabled'로 차단된다(권한 미들웨어가 즉시 무효화)."""
    if student.real_name is None and student.status == "disabled":
        return False  # 멱등 — 이미 파기됨
    student.real_name = None
    student.age = None
    student.birth_date = None  # 생년월일도 식별 PII — 함께 파기 (signup_age_01)
    student.guardian_email = None  # 보호자 연락처 파기 (동의 이력은 Consent 행으로 잔존)
    student.gender = None
    student.nickname = "탈퇴한 학생"
    student.avatar = {}
    # 로그인 ID·비번은 재사용/역추적 불가한 값으로 — unique 제약 유지 위해 student.id 파생.
    student.student_login_id = f"del_{student.id[:24]}"
    student.password_hash = ""  # 로그인 불가
    student.class_id = None  # 학급 명단에서 제외
    student.status = "disabled"
    return True


def anonymize_user(db: Session, user: User) -> bool:
    """학부모/교사/기관 사용자의 식별 PII(이메일·이름)를 파기. 이미 파기됐으면 False."""
    if user.status == "disabled" and (user.email or "").startswith("del_"):
        return False
    user.name = "탈퇴한 사용자"
    user.email = f"del_{user.id[:24]}@deleted.invalid"
    user.password_hash = ""
    user.status = "disabled"
    return True


def anonymize_stale_students(db: Session, inactive_days: int = 365) -> int:
    """보존기간(비활성 N일) 만료 학생을 자동 익명화 — 보존만료 파기 배치. 파기 건수 반환.

    대상: status='disabled'(비활성)이고 마지막 로그인이 inactive_days 이전이며 아직 실명이
    남아 있는(미파기) 학생. 크론/관리 커맨드에서 주기 실행(manage_privacy.py)."""
    cutoff = datetime.combine(date.today() - timedelta(days=inactive_days), datetime.min.time())
    rows = (
        db.query(StudentProfile)
        .filter(
            StudentProfile.status == "disabled",
            StudentProfile.real_name.isnot(None),
            (StudentProfile.last_login_at.is_(None)) | (StudentProfile.last_login_at < cutoff),
        )
        .all()
    )
    n = 0
    for s in rows:
        if anonymize_student(db, s):
            n += 1
    if n:
        db.commit()
    return n


def find_minor_students(db: Session) -> list[StudentProfile]:
    """만 14세 미만으로 '확인되는' 미파기 학생 목록 — 아동 테스트데이터 파기 대상.

    판정은 보수적으로 '알려진 미성년'만: 생년월일(만 나이<14) 또는 학교 입력 age(<14 —
    범위가 3~13이라 존재 자체가 미성년 확정). 연령 정보가 없는 계정은 건드리지 않는다
    (성인일 수 있음 — 별도 보고만). 이미 익명화된 계정(real_name=None+disabled)은
    anonymize_student가 멱등 스킵한다."""
    today = date.today()
    rows = db.query(StudentProfile).filter(
        (StudentProfile.birth_date.isnot(None)) | (StudentProfile.age.isnot(None))
    ).all()
    out = []
    for s in rows:
        if s.birth_date is not None:
            age = (
                today.year
                - s.birth_date.year
                - ((today.month, today.day) < (s.birth_date.month, s.birth_date.day))
            )
            if age < 14:
                out.append(s)
        elif s.age is not None and s.age < 14:
            out.append(s)
    return out


def purge_minor_students(db: Session) -> int:
    """만 14세 미만(확인된) 학생 전원 익명화 — 실서비스 전 아동 테스트데이터 파기.

    (사용자 결정 0716·실행 승인 0718.) 행동데이터는 절대 삭제하지 않는다 —
    anonymize_student는 학습기록·behavior_summaries를 건드리지 않고(soft ref 유지),
    기존 축적분은 성인(팀원) 생성이라 actor_band='adult'로 태깅된 채 계속 사용한다."""
    n = 0
    for s in find_minor_students(db):
        if anonymize_student(db, s):
            n += 1
    if n:
        db.commit()
    return n
