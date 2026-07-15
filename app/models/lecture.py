from datetime import datetime

from sqlalchemy import (
    CHAR,
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class Lecture(Base, UUIDPk, Timestamps):
    """강의 영상 메타 — 파일 경로는 저장하지 않고 LECTURE_MEDIA_DIR/{id}{video_ext}로 유도(경로조작 원천 차단)"""

    __tablename__ = "lectures"
    __table_args__ = (
        Index("ix_lecture_subject_status", "subject", "status"),
        Index("ix_lecture_created", "created_at"),
    )

    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject: Mapped[str] = mapped_column(String(20))  # 국어|영어|수학|과학|사회|생활
    video_ext: Mapped[str] = mapped_column(String(10))  # .mp4|.webm
    video_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    duration_sec: Mapped[int] = mapped_column(default=0)
    check_min_sec: Mapped[int] = mapped_column(default=60)  # 체크포인트 간격 최소(초)
    check_max_sec: Mapped[int] = mapped_column(default=180)  # 체크포인트 간격 최대(초)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|hidden|deleted
    # 과목 내 목차 순서(1강·2강…) — 목록·목차는 (subject, order_no, created_at) 오름차순.
    # 생성 시 미지정이면 그 과목의 max+1로 맨 뒤 배정(운영자가 PUT으로 재배열).
    order_no: Mapped[int] = mapped_column(default=0)
    uploaded_by: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("users.id"), nullable=True
    )


class LectureMaterial(Base, UUIDPk, Timestamps):
    """강의 자료(자료실) — file 종류는 경로를 저장하지 않고 LECTURE_MEDIA_DIR/materials/{id}{file_ext}로
    유도(영상과 동일 원칙 — 경로조작 원천 차단). link 종류는 url에 외부 URL만 담는다."""

    __tablename__ = "lecture_materials"
    __table_args__ = (Index("ix_lm_lecture_order", "lecture_id", "order_no"),)

    lecture_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("lectures.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(10))  # file|link
    # link면 외부 URL 원문, file이면 서버가 유도하는 다운로드 경로 키(/lectures/{lid}/materials/{id}/download)
    url: Mapped[str] = mapped_column(String(500))
    file_ext: Mapped[str | None] = mapped_column(String(10), nullable=True)  # file 종류만(.pdf 등)
    file_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    order_no: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|deleted


class LectureQuestion(Base, UUIDPk, Timestamps):
    """강의 확인 문항 — 정답(answer_index)은 payload와 분리 저장해 목록/상세 응답의 정답 유출을 구조적으로 차단"""

    __tablename__ = "lecture_questions"
    __table_args__ = (Index("ix_lq_lecture_pos", "lecture_id", "position_sec"),)

    lecture_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("lectures.id"), index=True)
    position_sec: Mapped[int] = mapped_column(default=0)  # 이 문항이 다루는 강의 시점
    # 출제 시점 — 강사가 고르는 세 가지 방식
    #   pinned=False(기본): position_sec 이후 아무 확인에서나 무작위로 뽑히는 '풀' 문항
    #   pinned=True, window_sec=0: 학생이 position_sec에 닿는 순간 반드시 이 문항이 뜬다
    #   pinned=True, window_sec>0: [position_sec, position_sec+window_sec] 구간 안에서
    #     서버가 고른 무작위 시점에 뜬다
    # 고정은 강사만 아는 정보를 쓴다 — "이 대목 직후에 물어야 방금 본 사람만 답한다".
    # 구간은 거기에 예측 불가능성을 더한다: 강사는 '문제 풀이 대목'만 지정하고 정확한 초는
    # 서버가 고른다. 매번 같은 초에 뜨면 학생이 그 지점만 외워 대기하는 학습이 생긴다.
    # 풀 문항과 고정 문항은 서로 배타적으로 뽑힌다(고정이 무작위 확인에 새어나오면
    # 지정 시점에 낼 문제가 사라진다).
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    window_sec: Mapped[int] = mapped_column(default=0)
    # {prompt, options[], explain} + 이미지 문항 확장(선택): prompt_image={id, ext},
    # option_images={"<보기 인덱스>": {id, ext}}. 파일 경로는 저장하지 않고
    # LECTURE_MEDIA_DIR/questions/{id}{ext}로 유도한다(영상·자료와 동일 — 경로조작 원천 차단).
    payload: Mapped[dict] = mapped_column(JSON)
    answer_index: Mapped[int] = mapped_column()  # options 내 정답 인덱스 — payload와 분리
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual|llm
    status: Mapped[str] = mapped_column(String(20), default="active")  # draft|active|deleted
    order_no: Mapped[int] = mapped_column(default=0)


class LectureWatchProgress(Base, UUIDPk, Timestamps):
    """학생별 강의 시청 진행(서버 정본) — watched_max는 하트비트 검증(속도상한·체크포인트 클램프)으로만 전진"""

    __tablename__ = "lecture_watch_progress"
    __table_args__ = (
        UniqueConstraint("student_id", "lecture_id", name="uq_lecture_watch"),
    )

    student_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("student_profiles.id"), index=True
    )
    lecture_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("lectures.id"), index=True)
    watched_max_sec: Mapped[int] = mapped_column(default=0)
    next_checkpoint_sec: Mapped[int | None] = mapped_column(nullable=True)  # None=남은 체크포인트 없음
    checkpoints_passed: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(20), default="watching")  # watching|done
    # 동시접속 차단(사업주 직업능력개발훈련 지원규정 별표1 — 동일 ID 동시접속 방지) —
    # 학생당 활성 시청 세션은 1개. session_id는 재생 시작(POST /session·takeover) 시
    # '서버가' new_uuid로 발급하는 값 — 클라이언트에는 서명 토큰으로만 전달되고,
    # 클라가 지어낸 식별자는 어떤 경로로도 이 컬럼에 닿지 않는다(담합 위장 차단).
    # last_heartbeat_at 기준 SESSION_TTL_SEC(30초) 무하트비트면 죽은 세션으로 자동 간주.
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # _now() 로컬 naive
    # 행동 기반 캡차 트리거 — 상호작용 면제 연속 횟수(캡차 통과 시 0으로 리셋)
    exempt_streak: Mapped[int] = mapped_column(default=0)
    # 의심 이벤트 누적(안 본 구간 seek/과속 하트비트/탭 백그라운드 자기신고) — 체크포인트 간격 축소에 사용
    suspicion: Mapped[int] = mapped_column(default=0)


class LectureCheckpointEvent(Base, UUIDPk, Timestamps):
    """체크포인트 캡차 시도 이력 — 통과/실패 감사·통계용(개별 학생 PII는 ops에 노출하지 않는다)"""

    __tablename__ = "lecture_checkpoint_events"
    __table_args__ = (Index("ix_lce_student_created", "student_id", "created_at"),)

    student_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("student_profiles.id"))
    lecture_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("lectures.id"), index=True)
    position_sec: Mapped[int] = mapped_column(default=0)
    result: Mapped[str] = mapped_column(String(20))  # passed|failed|exempted
