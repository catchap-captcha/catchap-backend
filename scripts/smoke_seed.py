"""로컬 스모크 테스트 전용 시드 — SQLite(dev_cat.db)에 운영자/강사/학생 + 코스/강의/문항/
시청 데이터를 채워 신규 콘솔 페이지 4종(코스 관리·문항 검수·학습 분석·시스템 상태)이
빈 화면이 아니라 실제 데이터로 렌더되는지 눈으로 확인하기 위한 것. 커밋 대상 아님(로컬 검증용).
"""

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# SQLite는 isolation_level="READ COMMITTED"를 지원하지 않는다(app/db/session.py는 MySQL
# 전용으로 이 값을 하드코딩). 리포 파일은 건드리지 않고, sqlite URL일 때만 그 kwarg를
# 제거하도록 create_engine을 몽키패치한다(로컬 스모크 전용 — 운영 경로에는 영향 없음).
import sqlalchemy  # noqa: E402

_orig_create_engine = sqlalchemy.create_engine


def _smoke_create_engine(url, *args, **kwargs):
    if str(url).startswith("sqlite") and kwargs.get("isolation_level") == "READ COMMITTED":
        kwargs.pop("isolation_level")
    return _orig_create_engine(url, *args, **kwargs)


sqlalchemy.create_engine = _smoke_create_engine

from app.db.base import Base  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.core.security import hash_password, new_uuid  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

# 모델을 전부 import(=Base.metadata에 테이블 등록)한 '다음'에 create_all해야 한다 — 반대
# 순서면 아직 등록 안 된 빈 메타데이터로 create_all이 돌아 "no such table" 오류가 난다.
from app.models import (  # noqa: E402
    Course,
    CourseCompletion,
    CourseEnrollment,
    Lecture,
    LectureQuestion,
    LectureWatchProgress,
    StudentProfile,
    User,
)
from app.models.course_exam import CourseExamAttempt, CourseExamQuestion  # noqa: E402

Base.metadata.create_all(engine)

with Session(engine) as db:
    ops = db.query(User).filter(User.email == "ops@smoke.dev").first()
    if ops is None:
        ops = User(
            email="ops@smoke.dev",
            password_hash=hash_password("Smoke1234!"),
            name="스모크운영자",
            role="ops",
            status="active",
            email_verified_at=dt.datetime.now(),
        )
        db.add(ops)

    inst = db.query(User).filter(User.email == "inst@smoke.dev").first()
    if inst is None:
        inst = User(
            email="inst@smoke.dev",
            password_hash=hash_password("Smoke1234!"),
            name="스모크강사",
            role="instructor",
            status="active",
            email_verified_at=dt.datetime.now(),
        )
        db.add(inst)
    db.flush()

    course = db.query(Course).filter(Course.title == "스모크 코스").first()
    if course is None:
        course = Course(
            instructor_id=inst.id,
            subject="일반",
            category="수학",
            title="스모크 코스",
            description="로컬 확인용 코스",
            order_no=1,
            status="active",
        )
        db.add(course)
        db.flush()

    lec1 = db.query(Lecture).filter(Lecture.title == "1강 스모크").first()
    if lec1 is None:
        lec1 = Lecture(
            title="1강 스모크",
            description="확인용 1강",
            subject="일반",
            course_id=course.id,
            video_ext=".mp4",
            video_bytes=1000,
            duration_sec=600,
            status="active",
            order_no=1,
            uploaded_by=inst.id,
        )
        db.add(lec1)
    lec2 = db.query(Lecture).filter(Lecture.title == "2강 스모크").first()
    if lec2 is None:
        lec2 = Lecture(
            title="2강 스모크",
            description="확인용 2강",
            subject="일반",
            course_id=course.id,
            video_ext=".mp4",
            video_bytes=1000,
            duration_sec=800,
            status="active",
            order_no=2,
            uploaded_by=inst.id,
        )
        db.add(lec2)
    db.flush()

    # 확인문항 — 1강엔 active 1개 + draft 2개(검수 큐 확인용), 2강엔 draft 1개
    def _q(lec_id, prompt, status, position, suggested=None, solver_passed=None):
        payload = {
            "prompt": prompt,
            "options": ["1번", "2번", "3번", "4번"],
            "explain": "스모크 확인용 해설",
        }
        if suggested:
            payload["suggested_placement"] = suggested
        if solver_passed is not None:
            payload["solver_passed"] = solver_passed
        return LectureQuestion(
            lecture_id=lec_id,
            position_sec=position,
            content_start_sec=max(0, position - 30),
            payload=payload,
            answer_index=0,
            source="manual",
            status=status,
            order_no=0,
        )

    existing_q = db.query(LectureQuestion).filter(LectureQuestion.lecture_id == lec1.id).count()
    if existing_q == 0:
        db.add(_q(lec1.id, "1강 활성 문항입니다", "active", 60))
        db.add(_q(lec1.id, "1강 검수 대기 문항 A (AI 생성)", "draft", 200, suggested="captcha", solver_passed=False))
        db.add(_q(lec1.id, "1강 검수 대기 문항 B (AI 생성)", "draft", 400, suggested="bank", solver_passed=True))
    existing_q2 = db.query(LectureQuestion).filter(LectureQuestion.lecture_id == lec2.id).count()
    if existing_q2 == 0:
        db.add(_q(lec2.id, "2강 검수 대기 문항 (불량 의심)", "draft", 300, suggested="discard", solver_passed=True))
    db.flush()

    # 학생 + 수강신청 + 시청 진행(완주/진행중) — 학습 분석 데이터
    stu = db.query(StudentProfile).filter(StudentProfile.student_login_id == "smoke_student@test.dev").first()
    if stu is None:
        stu = StudentProfile(
            student_login_id="smoke_student@test.dev",
            student_code="SMK-0001",
            password_hash=hash_password("Smoke1234!"),
            nickname="스모크학생",
            grade_band="adult",
            status="good",
        )
        db.add(stu)
        db.flush()

    if db.query(CourseEnrollment).filter(CourseEnrollment.student_id == stu.id).count() == 0:
        db.add(
            CourseEnrollment(
                student_id=stu.id, course_id=course.id, status="active", enrolled_at=dt.datetime.now()
            )
        )

    if db.query(LectureWatchProgress).filter(LectureWatchProgress.lecture_id == lec1.id).count() == 0:
        db.add(
            LectureWatchProgress(
                student_id=stu.id,
                lecture_id=lec1.id,
                watched_max_sec=600,
                checkpoints_passed=1,
                status="done",
            )
        )
    if db.query(LectureWatchProgress).filter(LectureWatchProgress.lecture_id == lec2.id).count() == 0:
        db.add(
            LectureWatchProgress(
                student_id=stu.id,
                lecture_id=lec2.id,
                watched_max_sec=300,
                checkpoints_passed=0,
                status="watching",
            )
        )

    # 코스 수료 시험 문항 + 응시 이력 + 수료(학습 분석 코스별 요약 데이터)
    examq = db.query(CourseExamQuestion).filter(CourseExamQuestion.course_id == course.id).first()
    if examq is None:
        examq = CourseExamQuestion(
            course_id=course.id,
            prompt="스모크 시험 문항",
            options=["A", "B", "C", "D"],
            answer_indexes=[0],
            explain="해설",
            origin="manual",
            status="active",
            order_no=1,
            created_by=inst.id,
        )
        db.add(examq)
        db.flush()

    if db.query(CourseExamAttempt).filter(CourseExamAttempt.student_id == stu.id).count() == 0:
        db.add(
            CourseExamAttempt(
                student_id=stu.id,
                course_id=course.id,
                question_id=examq.id,
                sitting_id=new_uuid(),
                result="correct",
            )
        )

    if db.query(CourseCompletion).filter(CourseCompletion.student_id == stu.id).count() == 0:
        db.add(
            CourseCompletion(
                student_id=stu.id,
                course_id=course.id,
                passed_at=dt.datetime.now(),
                question_count=1,
                sittings_count=1,
                perfect=True,
            )
        )

    db.commit()

print("시드 완료")
print("운영자 로그인: ops@smoke.dev / Smoke1234!  (역할: ops)")
print("강사 로그인:   inst@smoke.dev / Smoke1234!  (역할: instructor)")
