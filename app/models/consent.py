from datetime import datetime

from sqlalchemy import CHAR, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class Consent(Base, UUIDPk, Timestamps):
    """아동 개인정보 처리 동의 — 법정대리인(보호자) 동의 기록·증빙 (PIPA).

    사용자 결정 2026-07-13: 보호자가 자녀 계정 연동 시 직접 동의한다. 누가·언제·어떤 약관
    버전에 동의했는지 남겨 감사·증빙에 쓴다. 철회 시 withdrawn_at을 채운다(활성 동의 =
    withdrawn_at IS NULL). (동의 항목·약관 문구 자체는 법무 확정 필요 — terms_version으로 추적.)
    """

    __tablename__ = "consents"

    student_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("student_profiles.id"), index=True
    )
    # 무소속(이메일 가입) 학생의 가입 동의는 기관이 없다 — nullable (signup_age_01)
    organization_id: Mapped[str | None] = mapped_column(CHAR(36), index=True, nullable=True)
    # 동의 주체 = 법정대리인(보호자) 사용자 id. 가입 시점 보호자 동의(signup_guardian)는
    # 보호자 계정이 아직 없어 None — 증빙은 StudentProfile.guardian_email(코드 인증)로 남는다.
    granted_by_user_id: Mapped[str | None] = mapped_column(CHAR(36), index=True, nullable=True)
    # 동의 유형 — personal_info(수집·이용) / third_party(제3자제공) / external_export 등
    consent_type: Mapped[str] = mapped_column(String(40), default="personal_info")
    terms_version: Mapped[str] = mapped_column(String(20), default="v1")
    granted_at: Mapped[datetime] = mapped_column(DateTime)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
