from datetime import datetime

from sqlalchemy import CHAR, JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class Organization(Base, UUIDPk, Timestamps):
    """등록된 고객 기관 (햇살초 등)"""

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(150))
    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)  # HS-EDU-2041
    org_type: Mapped[str] = mapped_column(String(30), default="초등학교")
    status: Mapped[str] = mapped_column(String(20), default="active")  # pending|active|disabled
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    business_number: Mapped[str | None] = mapped_column(String(30), unique=True, nullable=True)
    code_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 이 기관이 구매한 교육형 과목 목록(예: ["국어"]). 외부 API 키 발급 허용 범위 —
    # 기관 관리자는 이 안에서만 키를 만들 수 있고, 운영자가 판매 시 설정한다.
    edu_subjects: Mapped[list] = mapped_column(JSON, default=list)


class OrgRegistrationRequest(Base, UUIDPk, Timestamps):
    """기관 가입 신청 (로그인 화면: 신청→접수 PENDING→요금제→계약). 1차: 자동 승인."""

    __tablename__ = "org_registration_requests"

    org_name: Mapped[str] = mapped_column(String(150))
    org_type: Mapped[str] = mapped_column(String(30))
    business_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_name: Mapped[str] = mapped_column(String(100))
    contact_email: Mapped[str] = mapped_column(String(255))
    contact_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    expected_students: Mapped[str | None] = mapped_column(String(30), nullable=True)
    plan_interest: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|approved|rejected
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    organization_id: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)
    memo: Mapped[str | None] = mapped_column(Text, nullable=True)


class Institution(Base, UUIDPk, Timestamps):
    """InstitutionPicker 검색용 기관 디렉토리 (시도>시군구>동 드릴다운)"""

    __tablename__ = "institutions"

    name: Mapped[str] = mapped_column(String(150), index=True)
    inst_type: Mapped[str] = mapped_column(String(30))  # 초등학교|유치원|어린이집
    sido: Mapped[str] = mapped_column(String(30), index=True)
    sigungu: Mapped[str] = mapped_column(String(30), index=True)
    dong: Mapped[str] = mapped_column(String(30), index=True)
    road_address: Mapped[str] = mapped_column(String(255))
    organization_id: Mapped[str | None] = mapped_column(
        CHAR(36), nullable=True
    )  # 등록 기관과 연결되면 세팅
