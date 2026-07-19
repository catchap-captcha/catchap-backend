"""코스 수료 시험 — 완전학습(mastery) 모델 4종.

설계: docs/course-exam-design.md (사용자 결정 2026-07-18 — 기출=비영리 교육용·
완전학습 통과). 학습 루프의 마지막 조각:
  배움(강의 시청 검증) → 연습(문제은행 Q) → 증명(수료 시험 mastery)

공부 키워드: mastery learning(완전학습 — 틀린 것만 재출제, 누적 전 문항 정답=수료),
server-side permutation(보기 셔플 순열을 서버에 보관해 위치 위조 차단),
soft reference(FK 없는 참조 — 라이브 덤프 DB collation 불일치 선례).

정복(mastered) 집합은 별도 상태 테이블 없이 course_exam_attempts에서 파생한다 —
상태·기록 이중화는 동기화 버그의 원천(문제은행이 LearningAttempt에서 분류를
파생하는 것과 동형).
"""

from datetime import datetime

from sqlalchemy import CHAR, JSON, DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class CourseExamQuestion(Base, UUIDPk, Timestamps):
    """시험 문항 — 강의 문항과 같은 인덱스 기반 형식(options + answer_indexes).

    origin=past_exam(기출)은 source(출처 문구)가 필수(엔드포인트 400) — 비영리
    교육용 이용 전제(설계 §2)라 화면(카드·결과지)에 항상 노출한다. ★유료화 시
    이 전제가 깨진다(라이선스 확보 또는 기출 문항 내리기 — 설계 문서에 결정 기록)."""

    __tablename__ = "course_exam_questions"
    __table_args__ = (Index("ix_ceq_course_status", "course_id", "status"),)

    course_id: Mapped[str] = mapped_column(CHAR(36), index=True)  # 소프트 참조(courses)
    prompt: Mapped[str] = mapped_column(Text)
    options: Mapped[list] = mapped_column(JSON)  # ["보기1", "보기2", ...] 2~6개
    answer_indexes: Mapped[list] = mapped_column(JSON)  # [0] 단일 / [0,2] 다답(집합 일치)
    explain: Mapped[str | None] = mapped_column(Text, nullable=True)
    # manual(자작) | past_exam(기출) | lecture(강의 문항 복사, 1.5단계) | llm(2단계)
    origin: Mapped[str] = mapped_column(String(20), default="manual")
    source: Mapped[str | None] = mapped_column(String(300), nullable=True)  # 출처 문구
    origin_lecture_question_id: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)
    order_no: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(10), default="draft")  # draft|active|deleted
    created_by: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)


class CourseExamSitting(Base, UUIDPk, Timestamps):
    """응시 회차 — 한 번에 최대 EXAM_SITTING_SIZE(10)문항.

    questions(JSON) = [{"question_id", "order": [2,0,3,1]}] — 보기 셔플 순열의 서버
    정본. 학생은 '표시 순서 기준 선택'을 제출하고 서버가 원본 인덱스로 복원해
    채점한다(답 위치 암기·위조 차단). 학생·코스당 미제출 회차는 1개 — 재요청 시
    기존 회차를 돌려줘 새로고침 파밍(문항 조합 다시 굴리기)을 차단."""

    __tablename__ = "course_exam_sittings"
    __table_args__ = (Index("ix_ces_student_course", "student_id", "course_id"),)

    course_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    student_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    questions: Mapped[list] = mapped_column(JSON)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    total: Mapped[int | None] = mapped_column(nullable=True)  # 제출 시 채움
    correct: Mapped[int | None] = mapped_column(nullable=True)


class CourseExamAttempt(Base, UUIDPk, Timestamps):
    """문항 응답 기록(원장) — 정복(mastered) 집합의 파생 원천.

    정복 = 이 테이블에 result='correct'가 1건이라도 있는 question_id. 시험 응답은
    문제은행 정답률·LearningAttempt에 반영하지 않는다(설계 §7 — 재시험 루프라
    정답률을 오염시킴)."""

    __tablename__ = "course_exam_attempts"
    __table_args__ = (Index("ix_cea_student_course_q", "student_id", "course_id", "question_id"),)

    student_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    course_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    question_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    sitting_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    result: Mapped[str] = mapped_column(String(10))  # correct | incorrect
    answer: Mapped[list | None] = mapped_column(JSON, nullable=True)  # 원본 인덱스 기준 선택
    solve_time_ms: Mapped[int] = mapped_column(default=0)


class CourseCompletion(Base, UUIDPk, Timestamps):
    """수료 기록 — 전 활성 문항 누적 정답 달성 시점의 스냅샷.

    수료 후 강사가 문항을 추가해도 수료는 유지된다(passed_at 고정 — 재잠금은
    학습자 신뢰를 깨므로 하지 않음, 상용 인강 표준). perfect = 수료 시점까지 이
    코스 시험에서 오답 기록이 0(모든 문항을 첫 시도 정답)."""

    __tablename__ = "course_completions"
    __table_args__ = (
        UniqueConstraint("student_id", "course_id", name="uq_completion_student_course"),
    )

    student_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    course_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    passed_at: Mapped[datetime] = mapped_column(DateTime)
    question_count: Mapped[int] = mapped_column(default=0)  # 수료 시점 활성 문항 수 스냅샷
    sittings_count: Mapped[int] = mapped_column(default=0)
    perfect: Mapped[bool] = mapped_column(default=False)
