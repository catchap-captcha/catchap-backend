from datetime import datetime

from sqlalchemy import CHAR, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class Membership(Base, UUIDPk, Timestamps):
    """기관 소속 (교사/기관 관리자). 교사 개별코드(T-xxxx)·담당 정보 포함."""

    __tablename__ = "memberships"
    # 한 사용자가 한 기관에 소속행 1개 (동시 가입/임명 race로 중복 멤버십 차단).
    # user_id가 NULL(미클레임 교사코드)인 행은 UNIQUE 대상에서 제외됨(NULL != NULL) — 선발급 다수 허용.
    __table_args__ = (
        UniqueConstraint("user_id", "organization_id", name="uq_membership_user_org"),
    )

    # 교사 코드(T-xxxx) 선발급 → 가입 시 클레임 구조라 nullable
    user_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("users.id"), index=True, nullable=True
    )
    organization_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("organizations.id"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))  # teacher | grade_head | org_admin
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|pending|disabled
    teacher_code: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    position: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 담임 | 수학 전담 등
    # 교사 초대 시 지정한 담당 반 — 가입(코드 클레임) 시 이 반의 담임/보조로 자동 배정 후 비움.
    # (초대 시점엔 교사 계정이 없어 즉시 배정 불가한 경우를 위한 예약 슬롯)
    pending_class: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # 학년부장(grade_head)이 담당하는 학년(정수). teacher/org_admin은 NULL.
    # role=grade_head 인데 managed_grade 가 있으면 그 학년 범위만 관리 가능.
    managed_grade: Mapped[int | None] = mapped_column(nullable=True)
    career_years: Mapped[int | None] = mapped_column(nullable=True)
    invited_by: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)
    joined_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Invitation(Base, UUIDPk, Timestamps):
    __tablename__ = "invitations"

    organization_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("organizations.id"), index=True
    )
    email: Mapped[str] = mapped_column(String(255), index=True)
    role: Mapped[str] = mapped_column(String(20))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    invited_by: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # 초대 시 선발급한 교사 개별코드(T-xxxx) — accept 시 가입화면에 프리필된다
    teacher_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
