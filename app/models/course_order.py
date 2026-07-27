"""코스 수강 결제 주문(order) — 학생이 유료 코스를 결제해 수강신청하는 흐름의 서버 기록.

무료 자유 신청([[catchap-course-model]]의 CourseEnrollment)과 별개로, 결제가 필요한 코스는
'주문 생성(pending) → PG 승인 → 확정(paid) → 수강신청 활성화'의 2단계를 거친다. 이 테이블은
그 주문 한 건을 남긴다(감사·재확인·환불 근거). 결제 자체는 토스페이먼츠(TOSS_SECRET_KEY가
있으면 실제 승인, 없으면 mock 승인)로 처리하되, **금액은 주문 생성 시 서버가 확정해 저장**하고
확정(confirm) 때 클라이언트가 보낸 금액과 대조한다 — 프런트가 금액을 조작해도 승인되지 않게
하는 표준 방어(PG 연동의 핵심 계약).

소프트 참조(FK 제약 없이 인덱스만) — 이 코드베이스 규약(behavior_summaries.student_id와 동일).
order_uid는 PG에 넘기는 주문 식별자(토스 orderId)라 전역 유니크로 둔다."""

from datetime import datetime

from sqlalchemy import CHAR, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class CourseOrder(Base, UUIDPk, Timestamps):
    __tablename__ = "course_orders"
    __table_args__ = (
        # (학생, 코스, 상태) — 같은 학생의 그 코스에 살아있는 pending 주문을 재사용/조회할 때.
        Index("ix_order_student_course_status", "student_id", "course_id", "status"),
    )

    student_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    course_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    # PG에 넘기는 주문 식별자(토스 orderId) — 승인 콜백에서 이 값으로 주문을 되찾는다.
    order_uid: Mapped[str] = mapped_column(String(64), unique=True)
    # 결제 금액(원, 정수). 주문 생성 시 서버가 확정 — 확정(confirm) 시 대조해 위변조를 막는다.
    amount: Mapped[int] = mapped_column(default=0)
    # pending = 승인 대기 / paid = 결제 완료(수강신청 활성) / failed = 승인 실패 / cancelled = 취소
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # 결제 경로 — toss = 실제 토스 승인, mock = 키 미설정 시 모의 승인(로컬·데모)
    provider: Mapped[str] = mapped_column(String(20), default="mock")
    # 승인 후 PG가 준 결제 키(토스 paymentKey) — 환불·조회 근거. 승인 전엔 null.
    payment_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 결제 수단(카드/간편결제 등) — PG 승인 응답에서 채운다. mock이면 프런트 선택값.
    method: Mapped[str | None] = mapped_column(String(30), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 승인 실패 원인(정직 노출용) — 성공한 척 넘기지 않는다.
    fail_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
