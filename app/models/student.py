from datetime import datetime

from sqlalchemy import CHAR, JSON, Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class StudentProfile(Base, UUIDPk, Timestamps):
    """학생 (가명 중심 — 실명 대신 nickname).

    로그인: student_login_id + PW (기관 선택은 선택 — 미지정 시 아이디로 판별).
    이메일 가입 전환(2026-07-16): 새 학생은 이메일이 곧 student_login_id이고
    organization_id 없이(None) 가입한다. 기관 경유 가입 경로는 옵션으로 유지."""

    __tablename__ = "student_profiles"

    # 교사/기관이 비번 초기화하면 True → 첫 로그인 시 새 비번 설정 강제
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)

    # 이메일 가입 학생은 무소속(None) — 기관 경유 가입에서만 채워진다 (student_email_01)
    organization_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("organizations.id"), nullable=True, index=True
    )
    class_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("classes.id"), nullable=True, index=True
    )
    # 전역 유일 (사용자 결정 2026-07-04: 기관 간 아이디+비밀번호 충돌로 타인 계정
    # 접근 가능한 리스크 차단 — 가입 시 중복 확인 필수). 이메일이 아이디가 될 수 있어 255자.
    student_login_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    student_code: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # CAT-4823
    password_hash: Mapped[str] = mapped_column(String(255))
    nickname: Mapped[str] = mapped_column(String(50))  # 하은
    # 학교(기관)가 입력하는 실명 — 교사·기관 화면 표시/검색 전용.
    # 학생·학부모·랭킹 화면에는 절대 노출하지 않는다 (아이들 사이 가명 유지).
    real_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    age: Mapped[int | None] = mapped_column(nullable=True)
    # 성별 — 학습 분석·외부 익명 집계의 인구통계 축. male|female|other|None(미입력).
    gender: Mapped[str | None] = mapped_column(String(10), nullable=True)
    grade_band: Mapped[str] = mapped_column(String(30), default="kindergarten")
    avatar: Mapped[dict] = mapped_column(JSON, default=dict)  # {hat, background, sticker}
    coins: Mapped[int] = mapped_column(default=0)
    level: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(20), default="good")  # good|inactive|needs_help
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ParentStudentLink(Base, UUIDPk, Timestamps):
    """학부모-자녀 연결. DB는 요청/승인 구조, 1차 정책: 학생 코드 입력 시 자동 승인."""

    __tablename__ = "parent_student_links"
    # 같은 학부모-자녀 조합은 1행만 (동시 연결 요청 race로 중복 링크 생기던 것 차단)
    __table_args__ = (
        UniqueConstraint("parent_user_id", "student_id", name="uq_parent_student_link"),
    )

    parent_user_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("users.id"), index=True)
    student_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("student_profiles.id"), index=True
    )
    organization_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    status: Mapped[str] = mapped_column(String(20), default="approved")  # requested|approved|rejected|removed
    requested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)
    daily_goal: Mapped[int] = mapped_column(default=5)
    time_limit_enabled: Mapped[bool] = mapped_column(default=False)


class ClassAssignment(Base, UUIDPk, Timestamps):
    """반 배정 이력 — 학생이 어느 반에 언제부터 언제까지 소속됐는지(SIS 표준 enrollment 기록).

    StudentProfile.class_id는 '현재' 배정만 갖고 이력이 없어, 전학·반 이동 시
    "우리 반에 있던 기간의 학습 기록"을 자를 수 없었다. 배정 변경 지점마다
    record_class_assignment()가 열린 행을 닫고 새 행을 연다. ended_on IS NULL = 현재 배정.
    날짜는 KST 로컬(date.today()) 규약."""

    __tablename__ = "class_assignments"

    organization_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    student_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("student_profiles.id"), index=True
    )
    class_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("classes.id"), index=True)
    started_on: Mapped[datetime] = mapped_column(DateTime)  # 배정 시작(자정 KST)
    ended_on: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # NULL=현재
