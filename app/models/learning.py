from datetime import date, datetime

from sqlalchemy import (
    CHAR,
    JSON,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class StudentProgress(Base, UUIDPk, Timestamps):
    """과목/챕터별 진도"""

    __tablename__ = "student_progress"
    # 과목당 진도행 1개 (동시 학습 저장 race로 중복행 생겨 집계가 부풀던 것 차단)
    __table_args__ = (
        UniqueConstraint("student_id", "subject", name="uq_student_progress_subject"),
    )

    organization_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    student_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("student_profiles.id"), index=True
    )
    subject: Mapped[str] = mapped_column(String(20), index=True)
    chapters_done: Mapped[int] = mapped_column(default=0)
    current_chapter: Mapped[int] = mapped_column(default=1)
    questions_done: Mapped[int] = mapped_column(default=0)
    accuracy: Mapped[float] = mapped_column(Float, default=0)  # 0~100


class ChapterProgress(Base, UUIDPk, Timestamps):
    """전체학습 주간 챕터의 단계 진행(이어하기 커서) — 오늘의퀴즈(습관)와 분리된 '학습' 축.

    (student, subject, chapter_no)당 1행. stages_done(0~5) = 5단계 바 채움 + 재개 지점.
    5면 챕터 완료. 챕터 자체는 문제은행을 10문제(5단계×2)씩 자른 것(services/chapters.py),
    잠금 해제는 달력(월요일) 기준이라 여기 저장하지 않는다.
    """

    __tablename__ = "chapter_progress"
    __table_args__ = (
        UniqueConstraint("student_id", "subject", "chapter_no", name="uq_chapter_progress"),
    )

    student_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("student_profiles.id"), index=True
    )
    subject: Mapped[str] = mapped_column(String(20), index=True)
    chapter_no: Mapped[int] = mapped_column()
    stages_done: Mapped[int] = mapped_column(default=0)  # 0~5


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
    # 학생·날짜·과목당 1행 (동시 완료 저장 race로 done 행이 중복돼 랭킹 점수가 부풀던 것 차단)
    __table_args__ = (
        UniqueConstraint(
            "student_id", "quiz_date", "subject", name="uq_daily_quiz_student_date_subject"
        ),
    )

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
    # 운영 콘솔 행동 데이터 목록의 최신순 정렬/기간 집계 가속 (migration a7b8c9d0e1f2)
    __table_args__ = (Index("ix_bs_created", "created_at"),)

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
    # 입력 방식 — 궤적 모양이 기기별로 크게 다르므로 판정 모델의 핵심 축.
    # 수집 시점에만 알 수 있어 소급 복구 불가 → 지금부터 저장한다. mouse|touch|pen|unknown
    input_type: Mapped[str] = mapped_column(
        String(10), default="unknown", server_default="unknown"
    )
    # 지도학습용 정답 라벨 자리 — organic(실트래픽·미검증) 기본, 이후 bot(합성/자동화)·human(검증) 부여.
    sample_label: Mapped[str] = mapped_column(
        String(12), default="organic", server_default="organic"
    )
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 아동용 캡차 판정 모델 학습셋 큐레이션 상태 (운영 콘솔에서 관리)
    # server_default: seed의 bulk_insert_mappings처럼 ORM 기본값을 안 타는 INSERT도 안전하게
    dataset_status: Mapped[str] = mapped_column(
        String(20), default="candidate", server_default="candidate"
    )  # candidate|included|excluded


class BehaviorTrace(Base, UUIDPk, Timestamps):
    """원시 포인터 궤적 — 아동용 캡차 판정 모델의 학습 재료.

    behavior_summaries 1행당 최대 1행. points는 [[t_ms, x, y], ...]
    (t: 상호작용 시작 기준 ms, x/y: 캡처 영역 기준 0~1 정규화, 서버에서 2000점 캡).
    요약 지표(path_length 등)는 저장 시 서버가 이 궤적으로부터 직접 계산한다.
    """

    __tablename__ = "behavior_traces"

    behavior_id: Mapped[str] = mapped_column(CHAR(36), unique=True, index=True)
    points: Mapped[list] = mapped_column(JSON, default=list)
    point_count: Mapped[int] = mapped_column(default=0)
    duration_ms: Mapped[int] = mapped_column(default=0)
    box_w: Mapped[int] = mapped_column(default=0)  # 캡처 영역 px (좌표 복원용)
    box_h: Mapped[int] = mapped_column(default=0)


class ConceptRead(Base, UUIDPk, Timestamps):
    """개념설명 읽음 상태 (localStorage → 서버 동기화)"""

    __tablename__ = "concept_reads"

    student_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("student_profiles.id"), index=True
    )
    chapter_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("chapters.id"), index=True)
