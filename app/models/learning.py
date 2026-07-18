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

    # 무소속(이메일 가입) 학생 허용 — 기관 은퇴(제품 전환) 후 org는 선택이다.
    # NOT NULL이던 시절엔 무소속 학생의 채점 저장이 1048로 통째로 409났다(0719 라이브 실증).
    organization_id: Mapped[str | None] = mapped_column(CHAR(36), index=True, nullable=True)
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

    # 무소속(이메일 가입) 학생 허용 — orgless_learn_01 (위 StudentProgress 주석 참조)
    organization_id: Mapped[str | None] = mapped_column(CHAR(36), index=True, nullable=True)
    student_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("student_profiles.id"), index=True
    )
    subject: Mapped[str] = mapped_column(String(20), index=True)
    chapter_no: Mapped[int | None] = mapped_column(nullable=True)
    # 뱅크 문항 id — UUID가 아니라 'math-ch4_08_...'류 슬러그(최장 49자 관측)라 CHAR(36)이면
    # verify가 500(Data too long)났다. 여유를 두고 80자.
    content_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    result: Mapped[str] = mapped_column(String(20))  # correct | incorrect
    score: Mapped[int] = mapped_column(default=0)
    solve_time_ms: Mapped[int] = mapped_column(default=0)
    retry_count: Mapped[int] = mapped_column(default=0)
    estimated_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # 서버 채점 여부 — True는 위젯 verify·game-answer(서버가 정답 검증) 경로, False는
    # /learning/attempts 자기신고(비검증). 오늘의퀴즈 done 승격·랭킹·코인·스티커는 graded만
    # 근거로 삼아 무채점 자기신고 위조를 차단한다(0713 적대적 검토 #4/#5). 기존 행은 True로 백필.
    graded: Mapped[bool] = mapped_column(default=True, index=True)


class StudentQuestionState(Base, UUIDPk, Timestamps):
    """학생×문항 SRS(간격 반복) 상태 — 문제은행 '오늘의 큐'의 정본.

    설계: docs/question-bank-scale-design.md. 은행이 만 개로 커져도 학생이 오늘 마주하는
    문항을 작게 유지하기 위한 상태 기계다(공부 키워드: spaced repetition, mastery).

    - **행이 없으면 '안 푼(new)'** — 행은 실제 응답한 문항에만 생겨 희소하게 유지된다
      (풀이 만 개여도 500문제 푼 학생은 500행). 출제 조회가 풀 크기가 아니라 학생 이력
      크기에 비례하게 만드는 핵심.
    - LearningAttempt가 원장(원본 기록)이고 이 테이블은 서빙용 파생 상태다 — 유실돼도
      백필(manage_bank_srs.py)로 재구축 가능. 갱신은 서버 채점(graded) 응답만 반영.
    - 참조는 소프트(FK 없음) — 라이브 덤프로 재생성한 DB의 collation 불일치로 FK 생성이
      실패하는 문제의 선례(Course.instructor_id)를 따른다.
    """

    __tablename__ = "student_question_states"
    __table_args__ = (
        UniqueConstraint("student_id", "question_id", name="uq_sqs_student_question"),
        # 출제 큐 조회 축 — (학생, 과목)으로 그 학생의 상태 전부를 한 번에 가져온다
        Index("ix_sqs_student_subject", "student_id", "subject"),
    )

    student_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    # 은행 문항 슬러그 — LearningAttempt.content_id와 같은 폭(80자, 'lec-…' 슬러그 대응)
    question_id: Mapped[str] = mapped_column(String(80))
    subject: Mapped[str] = mapped_column(String(20))
    # learning(학습 중) | mastered(연속 2회 정답 — 오답노트 '2회 정답 승격'과 같은 리듬)
    state: Mapped[str] = mapped_column(String(10), default="learning")
    correct_streak: Mapped[int] = mapped_column(default=0)
    wrong_count: Mapped[int] = mapped_column(default=0)
    last_result: Mapped[str] = mapped_column(String(10))  # correct | incorrect
    # 다음 복습 만기(SRS 사다리 1·3·7·14·30일). 오답이면 NULL(즉시 재출제 후보라 만기 무의미)
    next_review_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WrongAnswer(Base, UUIDPk, Timestamps):
    """오답노트 항목"""

    __tablename__ = "wrong_answers"

    student_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("student_profiles.id"), index=True
    )
    # 무소속(이메일 가입) 학생 허용 — orgless_learn_01 (StudentProgress 주석 참조)
    organization_id: Mapped[str | None] = mapped_column(CHAR(36), index=True, nullable=True)
    subject: Mapped[str] = mapped_column(String(20), index=True)
    category: Mapped[str] = mapped_column(String(30))
    question: Mapped[str] = mapped_column(Text)
    my_answer: Mapped[str] = mapped_column(String(200))
    correct_answer: Mapped[str] = mapped_column(String(200))
    tip: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed: Mapped[bool] = mapped_column(default=False)
    wrong_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # 전체학습 주차 챕터 오답이면 그 챕터(≥1), 오늘의 퀴즈 오답이면 NULL — '약한 챕터 미복습
    # 오답' 진단(대시보드)에 쓴다. 정답으로 다시 맞히면 reviewed=True로 승격(복습 순환).
    chapter_no: Mapped[int | None] = mapped_column(nullable=True, index=True)


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
    # 행위자 연령대(behavior_actor_01) — 아동 데이터 파기 시 성인 생성분 보존 판별 축.
    # adult=만 14세 이상 / minor=만 14세 미만(아동, PIPA 동의 기준) / NULL=미상(익명 등).
    # 기존 축적분은 전부 성인(팀원) 생성이라는 사용자 확정(2026-07-17)으로 'adult' 백필.
    # sample_label(지도학습 정답표)과는 다른 축 — 재사용하지 않는다(bot 잠금과 충돌).
    actor_band: Mapped[str | None] = mapped_column(String(10), nullable=True)
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


class ScratchRecord(Base, UUIDPk, Timestamps):
    """연습장 필기 원본 — 학습 인사이트(본인·교사·보호자 재생). 아동 필적이라 민감 개인정보다.

    - strokes: [{color, width, points: [[t_ms, x, y], ...]}, ...] — JSON, 저장 제한 없음
      (원본 보존 방침 — 아이가 아무리 많이 그어도 다 저장). content_id·과목별로 조회.
    - 집계 지표(stroke_count·distance_px·first_write_ms·draw_ms)는 운영자 익명 집계·봇 신호용.
    - consent_retain: 보호자 '원본 보존 동의'. True면 탈퇴 후에도 원본 유지, False(기본)면
      탈퇴/보존기한 시 원본(strokes) 파기(집계 지표만 익명 보존). 필적은 익명화 불가하므로.
    """

    __tablename__ = "scratch_records"

    student_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("student_profiles.id"), index=True
    )
    organization_id: Mapped[str | None] = mapped_column(CHAR(36), index=True, nullable=True)
    subject: Mapped[str] = mapped_column(String(20), index=True)
    content_id: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
    strokes: Mapped[list] = mapped_column(JSON, default=list)
    stroke_count: Mapped[int] = mapped_column(default=0)
    distance_px: Mapped[int] = mapped_column(default=0)
    first_write_ms: Mapped[int] = mapped_column(default=0)
    draw_ms: Mapped[int] = mapped_column(default=0)
    # 원본 파기 여부 — 탈퇴/보존기한으로 원본(strokes)을 지웠으면 True(집계 지표는 남김).
    purged: Mapped[bool] = mapped_column(default=False)
    consent_retain: Mapped[bool] = mapped_column(default=False)


class ConceptRead(Base, UUIDPk, Timestamps):
    """개념설명 읽음 상태 (localStorage → 서버 동기화)"""

    __tablename__ = "concept_reads"

    student_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("student_profiles.id"), index=True
    )
    chapter_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("chapters.id"), index=True)
