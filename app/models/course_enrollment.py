"""학생 코스 수강신청(enrollment) — 학생이 '이 코스를 듣겠다'고 등록한 기록.

무료 이수·수료 검증형 서비스라 Coursera 무료 코스와 같은 방식(자유 신청·자유 취소, 결제·환불
없음)을 따른다. 관련: [[catchap-course-model]], [[catchap-positioning-verified-learning]].

취소(withdraw)해도 행을 지우지 않고 status='withdrawn'으로 두는 이유: 진행 이력(시청 진도·
수료·시험 응시)은 각자 별도 테이블에 보존되므로, 재신청 시 같은 행을 'active'로 되살리면
이전 진도를 그대로 이어갈 수 있다(재신청 이력도 남는다). (student_id, course_id)는 유니크라
학생당 코스당 1행을 upsert 한다. 소프트 참조(FK 없음) — 이 코드베이스 규약.
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
    # active = 수강 중 / withdrawn = 취소(내 코스에서 빠짐, 진행 이력은 보존)
    status: Mapped[str] = mapped_column(String(20), default="active")
    enrolled_at: Mapped[datetime] = mapped_column(DateTime)
