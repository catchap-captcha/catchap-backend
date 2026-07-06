from datetime import date, datetime

from sqlalchemy import CHAR, JSON, Date, DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class StudentProgress(Base, UUIDPk, Timestamps):
    """과목/챕터별 진도"""

    __tablename__ = "student_progress"

    organization_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    student_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("student_profiles.id"), index=True
    )
    subject: Mapped[str] = mapped_column(String(20), index=True)
    chapters_done: Mapped[int] = mapped_column(default=0)
    current_chapter: Mapped[int] = mapped_column(default=1)
    questions_done: Mapped[int] = mapped_column(default=0)
    accuracy: Mapped[float] = mapped_column(Float, default=0)  # 0~100


class LearningAttempt(Base, UUIDPk, Timestamps):
    __tablename__ = "learning_attempts"
    # 대시보드 기간 집계 가속용 복합 인덱스 (migration ce50a1b2c3d4)
    __table_args__ = (
        Index("ix_la_student_created", "student_id", "created_at"),
        Index("ix_la_org_created", "organization_id", "created_at"),
    )

    organization_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    student_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("student_profiles.id"), index=True
    )
    subject: Mapped[str] = mapped_column(String(20), index=True)
    chapter_no: Mapped[int | None] = mapped_column(nullable=True)
    content_id: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)
    result: Mapped[str] = mapped_column(String(20))  # correct | incorrect
    score: Mapped[int] = mapped_column(default=0)
    solve_time_ms: Mapped[int] = mapped_column(default=0)
    retry_count: Mapped[int] = mapped_column(default=0)
    estimated_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)


class WrongAnswer(Base, UUIDPk, Timestamps):
    """오답노트 항목"""

    __tablename__ = "wrong_answers"

    student_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("student_profiles.id"), index=True
    )
    organization_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    subject: Mapped[str] = mapped_column(String(20), index=True)
    category: Mapped[str] = mapped_column(String(30))
    question: Mapped[str] = mapped_column(Text)
    my_answer: Mapped[str] = mapped_column(String(200))
    correct_answer: Mapped[str] = mapped_column(String(200))
    tip: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed: Mapped[bool] = mapped_column(default=False)
    wrong_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class Recommendation(Base, UUIDPk, Timestamps):
    """취약문제추천 항목"""

    __tablename__ = "recommendations"

    student_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("student_profiles.id"), index=True
    )
    subject: Mapped[str] = mapped_column(String(20))
    chapter_no: Mapped[int] = mapped_column(default=1)
    priority: Mapped[str] = mapped_column(String(20), default="보통")  # 높음|보통|낮음
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active")


class DailyQuizStatus(Base, UUIDPk, Timestamps):
    """오늘의퀴즈 과목별 상태"""

    __tablename__ = "daily_quiz_status"

    student_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("student_profiles.id"), index=True
    )
    quiz_date: Mapped[date] = mapped_column(Date, index=True)
    subject: Mapped[str] = mapped_column(String(20))
    topic: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="todo")  # todo|doing|done
    reward_coins: Mapped[int] = mapped_column(default=10)


class LearningSummary(Base, UUIDPk, Timestamps):
    """기간 요약 (주간/월간 통계·차트 데이터 소스)"""

    __tablename__ = "learning_summaries"

    organization_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    student_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("student_profiles.id"), index=True
    )
    period_type: Mapped[str] = mapped_column(String(10))  # week|month|year
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    total_count: Mapped[int] = mapped_column(default=0)
    correct_count: Mapped[int] = mapped_column(default=0)
    average_solve_time_ms: Mapped[int] = mapped_column(default=0)
    streak_days: Mapped[int] = mapped_column(default=0)
    strength_tags: Mapped[dict] = mapped_column(JSON, default=dict)
    need_practice_tags: Mapped[dict] = mapped_column(JSON, default=dict)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)  # 차트용 시계열 blob


class BehaviorSummary(Base, UUIDPk, Timestamps):
    __tablename__ = "behavior_summaries"

    organization_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    student_id: Mapped[str | None] = mapped_column(CHAR(36), nullable=True, index=True)
    source_type: Mapped[str] = mapped_column(String(30), default="game")
    solve_time_ms: Mapped[int] = mapped_column(default=0)
    path_length: Mapped[float] = mapped_column(Float, default=0)
    avg_speed: Mapped[float] = mapped_column(Float, default=0)
    pause_count: Mapped[int] = mapped_column(default=0)
    retry_count: Mapped[int] = mapped_column(default=0)
    drop_distance_norm: Mapped[float] = mapped_column(Float, default=0)
    interaction_result: Mapped[str | None] = mapped_column(String(20), nullable=True)
    risk_level: Mapped[str] = mapped_column(String(20), default="low")  # low|review|elevated
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ConceptRead(Base, UUIDPk, Timestamps):
    """개념설명 읽음 상태 (localStorage → 서버 동기화)"""

    __tablename__ = "concept_reads"

    student_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("student_profiles.id"), index=True
    )
    chapter_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("chapters.id"), index=True)
