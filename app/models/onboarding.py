from datetime import datetime

from sqlalchemy import CHAR, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class StudentJoinCode(Base, UUIDPk, Timestamps):
    """학교 발급 1회용 학생 가입 코드.

    - login_id: 학교 발급·전역 유일. 코드에 내장(활성화 시 학생 로그인 아이디가 됨).
    - code_hash: 원문 저장 금지, sha256만. 학생이 코드 입력 시 hash 비교.
    - 활성화(코드 소비) 시 used_at 설정 + StudentProfile 생성.
    """

    __tablename__ = "student_join_codes"

    organization_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    class_id: Mapped[str | None] = mapped_column(CHAR(36), nullable=True, index=True)
    login_id: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    code_hash: Mapped[str] = mapped_column(String(64), index=True)
    class_label: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # 기관이 등록 시 입력한 실명 — 활성화되면 StudentProfile.real_name 으로 복사
    real_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 기관(선생님)이 입력한 성별 — 활성화 시 StudentProfile.gender 로 복사(아이가 고르지 않음)
    gender: Mapped[str | None] = mapped_column(String(10), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    student_id: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)  # 활성화 후 연결
    created_by: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)


class ParentInviteCode(Base, UUIDPk, Timestamps):
    """학교 발급 학부모 초대 코드 (학생 1명 귀속·고엔트로피·만료·N회 허용).

    B1(무단 연결) 해소: 학부모는 이 코드로만 자녀에 연결. 임의 학생코드 추측 불가.
    """

    __tablename__ = "parent_invite_codes"

    student_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    organization_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    code_hash: Mapped[str] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    max_uses: Mapped[int] = mapped_column(Integer, default=2)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)
