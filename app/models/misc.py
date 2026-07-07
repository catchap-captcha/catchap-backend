from datetime import datetime

from sqlalchemy import CHAR, JSON, DateTime, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class Notification(Base, UUIDPk, Timestamps):
    __tablename__ = "notifications"

    user_id: Mapped[str | None] = mapped_column(CHAR(36), index=True, nullable=True)
    student_id: Mapped[str | None] = mapped_column(CHAR(36), index=True, nullable=True)
    organization_id: Mapped[str | None] = mapped_column(CHAR(36), index=True, nullable=True)
    type: Mapped[str] = mapped_column(String(30))
    category: Mapped[str] = mapped_column(String(30), default="일반")
    title: Mapped[str] = mapped_column(String(150))
    message: Mapped[str] = mapped_column(Text)
    child_id: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)  # 학부모 알림 자녀 필터
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class UserSetting(Base, UUIDPk, Timestamps):
    """역할별 설정 JSON (학생 눈건강/알림/소리, 교사 수업환경, 학부모 알림/개인정보 등)"""

    __tablename__ = "user_settings"
    # 주체(user/student)별 설정행 1개 (동시 저장 race로 중복 설정행 차단)
    __table_args__ = (
        UniqueConstraint("subject_type", "subject_id", name="uq_user_setting_subject"),
    )

    subject_type: Mapped[str] = mapped_column(String(10))  # user | student
    subject_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)


class FamilyMessage(Base, UUIDPk, Timestamps):
    """가정안내: 교사 → 보호자 메시지"""

    __tablename__ = "family_messages"

    organization_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    teacher_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("users.id"), index=True)
    student_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("student_profiles.id"), index=True
    )
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="sent")  # sent|read
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Inquiry(Base, UUIDPk, Timestamps):
    """문의하기 접수 (운영자 답변은 InquiryReply에 1:N으로 누적)"""

    __tablename__ = "inquiries"

    inquiry_type: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(100))
    affiliation: Mapped[str | None] = mapped_column(String(150), nullable=True)
    email: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="received")  # received|resolved


class InquiryReply(Base, UUIDPk, Timestamps):
    """문의에 대한 운영자 답변 — 확인 후 여러 번 회신 가능(1문의 : N답변). 각 답변은 이메일 발송."""

    __tablename__ = "inquiry_replies"

    inquiry_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("inquiries.id"), index=True)
    body: Mapped[str] = mapped_column(Text)
    answered_by: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)
    email_status: Mapped[str] = mapped_column(String(20), default="sent")  # sent|dry_run|failed


class Report(Base, UUIDPk, Timestamps):
    __tablename__ = "reports"

    organization_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    student_id: Mapped[str | None] = mapped_column(CHAR(36), index=True, nullable=True)
    report_type: Mapped[str] = mapped_column(String(30), default="weekly")
    period_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="ready")
    file_url: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ReportDownloadLog(Base, UUIDPk, Timestamps):
    __tablename__ = "report_download_logs"

    report_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("reports.id"), index=True)
    user_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)


class AuditLog(Base, UUIDPk, Timestamps):
    __tablename__ = "audit_logs"

    organization_id: Mapped[str | None] = mapped_column(CHAR(36), index=True, nullable=True)
    actor_user_id: Mapped[str | None] = mapped_column(CHAR(36), index=True, nullable=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    target_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_id: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)
    before_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class EmailLog(Base, UUIDPk, Timestamps):
    __tablename__ = "email_logs"

    user_id: Mapped[str | None] = mapped_column(CHAR(36), index=True, nullable=True)
    to_email: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="sent")  # sent|failed|dry_run
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class SystemHealthLog(Base, UUIDPk, Timestamps):
    __tablename__ = "system_health_logs"

    service_name: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20), default="ok")
    latency_ms: Mapped[int] = mapped_column(default=0)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ModelVersion(Base, UUIDPk, Timestamps):
    """AI모델 화면: 모델 레지스트리 (읽기전용 표시)"""

    __tablename__ = "model_versions"

    category: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(60))
    version: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="정상")  # 정상|베타
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_on: Mapped[str | None] = mapped_column(String(30), nullable=True)


class CaptchaAsset(Base, UUIDPk, Timestamps):
    __tablename__ = "captcha_assets"

    organization_id: Mapped[str | None] = mapped_column(CHAR(36), index=True, nullable=True)
    file_url: Mapped[str] = mapped_column(String(255))
    file_name: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(30))
    category: Mapped[str | None] = mapped_column(String(30), nullable=True)
    ai_label: Mapped[str | None] = mapped_column(String(60), nullable=True)
    review_status: Mapped[str] = mapped_column(String(20), default="pending")
    approved_by: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)


class AiPrediction(Base, UUIDPk, Timestamps):
    __tablename__ = "ai_predictions"

    asset_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("captcha_assets.id"), index=True)
    model_version: Mapped[str] = mapped_column(String(30))
    predicted_label: Mapped[str] = mapped_column(String(60))
    confidence: Mapped[float] = mapped_column(Float, default=0)
    latency_ms: Mapped[int] = mapped_column(default=0)
