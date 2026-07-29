"""학생 코스 수강권 모델.

무료 신청 또는 결제 승인으로 활성화되며, 취소 시 행을 삭제하지 않고 withdrawn 상태로
보존한다. 학생·코스 조합은 한 행만 유지해 재신청과 결제 재처리를 멱등하게 처리한다.
"""

from datetime import datetime

from sqlalchemy import CHAR, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class CourseEnrollment(Base, UUIDPk, Timestamps):
    __tablename__ = "course_enrollments"
    __table_args__ = (
        Index("ix_enroll_student_course", "student_id", "course_id", unique=True),
    )

    student_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    course_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    enrolled_at: Mapped[datetime] = mapped_column(DateTime)
