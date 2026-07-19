from datetime import datetime

from sqlalchemy import (
    CHAR,
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class Course(Base, UUIDPk, Timestamps):
    """강사 코스 — 한 강사가 한 과목으로 묶는 강의 묶음(예: '수학 기초반').

    설계(사용자 결정 2026-07-18): **코스 = 과목 하나 고정.** 생성 시 subject를 정하고,
    그 코스에 담기는 모든 강의는 이 과목이어야 한다(강의 업로드/수정에서 검증).
    한 강사가 코스를 여러 개 만들 수 있다(예: '수학 기초반'·'수학 심화반').
    학생 화면 구조는 과목 → 강사별 코스 → 코스 안의 강의 순서(order_no)다.

    소유권: instructor_id == 그 강사. 운영자는 전체를 감독한다(강사 강의 스코프와 동일
    규약 — _get_ops_course가 강사에겐 남의 코스를 404로 흘리지 않는다). 인강 표준
    (인프런·클래스101 등)의 코스=한 강사·한 주제 모델과 같은 계열."""

    __tablename__ = "courses"
    __table_args__ = (Index("ix_course_subject_status", "subject", "status"),)

    # 소프트 참조(FK 제약 없이 인덱스만) — behavior_summaries.student_id와 같은 규약.
    # 이 코드베이스는 relationship을 안 쓰고 명시적 db.get/query로 조회하며, DB FK는
    # collation 일치를 강제해(라이브/로컬 collation 불일치 시 생성 실패) 이식성만 해친다.
    instructor_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    subject: Mapped[str] = mapped_column(String(20))  # 과목 고정 — 이 코스의 모든 강의가 이 과목
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 과목 안에서의 코스 정렬(학생 화면: 같은 과목의 코스들 순서). 미지정 시 max+1로 맨 뒤.
    order_no: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|hidden|deleted


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
    # 소속 코스 — 강사 코스 모델(course_tbl_01) 도입 전 강의는 NULL(미분류). 코스의
    # subject와 이 강의의 subject는 반드시 일치한다(업로드/수정에서 검증 — 코스=과목 고정).
    # 소프트 참조(FK 제약 없이 인덱스만) — Course.instructor_id와 같은 이유(collation 회피).
    course_id: Mapped[str | None] = mapped_column(CHAR(36), nullable=True, index=True)
    video_ext: Mapped[str] = mapped_column(String(10))  # .mp4|.webm
    video_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    duration_sec: Mapped[int] = mapped_column(default=0)
    # (제거됨 0717) check_min_sec/check_max_sec — 무작위 확인 간격. 출제 시점이 전부
    # 핀(문항의 position_sec)이 되면서 간격 개념 자체가 사라졌다(lecture_pin_02).
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
    """강의 확인 문항 — 정답(answer_index/answer_indexes)은 payload와 분리 저장해
    목록/상세 응답의 정답 유출을 구조적으로 차단"""

    __tablename__ = "lecture_questions"
    __table_args__ = (Index("ix_lq_lecture_pos", "lecture_id", "position_sec"),)

    lecture_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("lectures.id"), index=True)
    position_sec: Mapped[int] = mapped_column(default=0)  # 이 문항이 다루는 강의 시점
    # 이 문항이 다루는 '내용이 시작되는' 시점(초) — 오답 상한 도달 시 여기로 되감는다
    # (강사가 영상을 보며 지정 — "이 대목을 다시 보고 와야 답할 수 있다"의 그 대목 시작).
    # NULL = 미지정 → max(0, cp - REWIND_SEC) 폴백. 규약: 지정 시 0 <= 값 < position_sec
    # (생성/수정에서 검증 — cp 이상으로 '되감으면' 재시청 없이 재도전이 무한 반복된다).
    content_start_sec: Mapped[int | None] = mapped_column(nullable=True)
    # 출제 시점 — 모든 문항이 고정 핀(전부 핀 lecture_pin_02 → 고정만 lecture_pin_03, 0717):
    # 학생이 position_sec에 닿는 순간 반드시 이 문항이 뜬다. 핀은 강사만 아는 정보를
    # 쓴다 — "이 대목 직후에 물어야 방금 본 사람만 답한다".
    # position_sec 규약: active면 1 이상·영상 안(생성/수정 시 검증). draft는 0 허용 —
    # LLM 생성 문항이 '시점 미배치' 상태로 검수를 기다리는 자리다(활성화 때 강제된다).
    # (제거됨 0717) window_sec — [position, position+window] 안 무작위 초 출제(구간).
    #  되감기(cp-REWIND_SEC)가 cp 기준인데 구간은 cp가 내용 시점과 멀 수 있어 엉뚱한
    #  대목을 되감았다. 고정만 남기면 cp == position이라 어긋남이 구조적으로 사라진다
    #  (lecture_service '구간 출제: 제거됨' 주석 참조).
    # ('position 이후 아무 확인에서나'였던 pinned=False 풀 + check_min/max 무작위 간격은
    #  옛 시청-감시 설계의 잔재로 제거 — lecture_service의 '랜덤 간격: 제거됨' 주석 참조.)
    # {prompt, options[], explain} + 이미지 문항 확장(선택): prompt_image={id, ext},
    # option_images={"<보기 인덱스>": {id, ext}}. 파일 경로는 저장하지 않고
    # LECTURE_MEDIA_DIR/questions/{id}{ext}로 유도한다(영상·자료와 동일 — 경로조작 원천 차단).
    payload: Mapped[dict] = mapped_column(JSON)
    answer_index: Mapped[int] = mapped_column()  # options 내 정답 인덱스 — payload와 분리
    # 다답형 정답 인덱스 목록(중복 없음, 전부 options 범위 안) — NULL이면 [answer_index]로
    # 본다(하위호환 — 기존 행 무변경). 읽는 쪽 규약: ids = q.answer_indexes or [q.answer_index].
    # answer_indexes를 쓸 때도 answer_index에는 첫 값을 함께 채워 구버전 읽기 경로가 깨지지 않게 한다.
    answer_indexes: Mapped[list | None] = mapped_column(JSON, nullable=True)
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
    # (사용 중단 0716) 상호작용 면제 연속 횟수 — 면제 장치를 걷어내 아무 코드도 읽지 않는다.
    # 컬럼은 남긴다: 드롭은 되돌릴 수 없고 마이그레이션만 하나 늘 뿐, 남겨도 무해하다.
    # 제거 이유는 lecture_service의 '상호작용 면제: 제거됨' 주석 참조.
    exempt_streak: Mapped[int] = mapped_column(default=0)
    # (제거됨 0717) suspicion — 의심 누적으로 무작위 확인 간격을 좁히던 값. 간격 자체가
    # 사라져(전부 핀) 컬럼도 함께 드롭했다(lecture_pin_02). 값이 일시 카운터라 잃는 정보가 없다.
    # 한 체크포인트에서 연속 오답 횟수 — MAX_CHECKPOINT_FAILS에 닿으면 그 대목을 다시
    # 보도록 watched_max를 REWIND_SEC만큼 되감고 0으로 리셋한다. 되감기가 없으면 오답 →
    # 새 랜덤 문항이 무한 반복돼(풀 브루트포스) 봇이 대가 없이 정답 집합을 수확할 수 있다.
    # 되감기로 watched_max<cp가 되면 _lecture_challenge가 새 문항 발급을 409로 거부한다
    # (다시 시청해 cp까지 올라와야 다음 문항). 통과 시에도 0으로 리셋한다.
    checkpoint_fails: Mapped[int] = mapped_column(default=0)


class LectureCheckpointEvent(Base, UUIDPk, Timestamps):
    """체크포인트 캡차 시도 이력 — 통과/실패 감사·통계용(개별 학생 PII는 ops에 노출하지 않는다)"""

    __tablename__ = "lecture_checkpoint_events"
    __table_args__ = (Index("ix_lce_student_created", "student_id", "created_at"),)

    student_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("student_profiles.id"))
    lecture_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("lectures.id"), index=True)
    position_sec: Mapped[int] = mapped_column(default=0)
    result: Mapped[str] = mapped_column(String(20))  # passed|failed|exempted


class LectureTranscript(Base, UUIDPk, Timestamps):
    """강의 전사(자막) — LLM 문항 생성의 근거. 강사 제공(SRT/VTT/붙여넣기) 또는 자동 STT 결과.

    왜 별도 테이블인가: 전사 JSON은 길 수 있는데(1시간 강의=수백 세그먼트), lectures 행은
    목록 조회(학생 코스 목록·강사 강의 목록)에서 매번 SELECT된다 — 큰 컬럼을 lectures에
    붙이면 그 목록들이 통째로 무거워진다. 1:1 분리로 lectures를 가볍게 유지하고, 전사는
    문항 생성·자막 관리 때만 로드한다. 소프트 참조(FK 없음 — 라이브 덤프 collation 정합, 신규
    모델 규약).

    왜 이 기능(강사 제공 자막): 강사가 이미 정확한 자막(스크립트·전문 자막)을 가진 경우
    Whisper 자동 STT를 다시 도는 건 (1) 품질 하락(자동 전사<원본 자막) (2) 비용·시간 낭비
    (3) OpenAI 키 강제 (4) 25MB 한계로 긴 강의 실패. 강사 자막을 받으면 넷 다 해결된다.
    자동 STT 결과도 여기 저장(source=stt)해 재생성 때 재전사하지 않는다."""

    __tablename__ = "lecture_transcripts"
    __table_args__ = (UniqueConstraint("lecture_id", name="uq_lecture_transcript"),)

    lecture_id: Mapped[str] = mapped_column(CHAR(36), index=True)  # 소프트 참조(강의당 1개)
    # 세그먼트 [{start, end, text}] — LLM(_prompt·_solve_prompt)이 먹는 포맷 그대로
    segments: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(20))  # srt|vtt|paste|stt
    segment_count: Mapped[int] = mapped_column(default=0)  # 목록 배지용 비정규화(JSON 미로드)
