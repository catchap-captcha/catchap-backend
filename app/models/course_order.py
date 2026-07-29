"""코스 수강 결제 주문(order) — 학생이 유료 코스를 결제해 수강신청하는 흐름의 서버 기록.

무료 자유 신청([[catchap-course-model]]의 CourseEnrollment)과 별개로, 결제가 필요한 코스는
'주문 생성(pending) → PG 승인 → 확정(paid) → 수강신청 활성화'의 2단계를 거친다. 이 테이블은
그 주문 한 건을 남긴다(감사·재확인·환불 근거). 결제는 토스페이먼츠 또는 카카오페이로
처리하며 mock은 개발 환경에서만 허용한다. **금액은 주문 생성 시 서버가 확정해 저장**하고
승인 때 PG 응답의 주문번호·금액·상태와 대조한다 — 프런트가 금액을 조작해도 승인되지 않게
하는 표준 방어(PG 연동의 핵심 계약).

소프트 참조(FK 제약 없이 인덱스만) — 이 코드베이스 규약(behavior_summaries.student_id와 동일).
order_uid는 PG에 넘기는 주문 식별자(토스 orderId)라 전역 유니크로 둔다."""

from datetime import datetime

from sqlalchemy import CHAR, JSON, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class CourseOrder(Base, UUIDPk, Timestamps):
    __tablename__ = "course_orders"
    __table_args__ = (
        # (학생, 코스, 상태) — 같은 학생의 그 코스에 살아있는 pending 주문을 재사용/조회할 때.
        Index("ix_order_student_course_status", "student_id", "course_id", "status"),
        Index("ix_order_provider_payment_key", "provider", "payment_key"),
    )

    student_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    course_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    # PG에 넘기는 주문 식별자(토스 orderId) — 승인 콜백에서 이 값으로 주문을 되찾는다.
    order_uid: Mapped[str] = mapped_column(String(64), unique=True)
    # 결제 금액(원, 정수). 주문 생성 시 서버가 확정 — 확정(confirm) 시 대조해 위변조를 막는다.
    amount: Mapped[int] = mapped_column(default=0)
    # pending=승인 대기 / paid=결제 완료 / failed=실패 / cancelled=승인 전 취소
    # refunded=전액 환불 / partially_refunded=외부 관리자에서 부분 환불(수강권 유지·운영 확인)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # 결제 경로 — toss | kakaopay | mock(개발 환경만)
    provider: Mapped[str] = mapped_column(String(20), default="mock")
    # PG 결제 식별값 — 토스 paymentKey 또는 카카오페이 TID. 환불·조회 근거.
    payment_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 결제 수단(카드/간편결제 등) — PG 승인 응답에서 채운다. mock이면 프런트 선택값.
    method: Mapped[str | None] = mapped_column(String(30), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 승인 실패 원인(정직 노출용) — 성공한 척 넘기지 않는다.
    fail_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 카카오페이 redirect 콜백 위조 방지 state 원문은 저장하지 않고 SHA-256 해시만 저장한다.
    callback_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 카카오페이 ready 응답의 만료성 redirect URL들. 비밀 키·결제수단 정보는 넣지 않는다.
    provider_session: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # PG가 제공한 영수증 URL(토스). 카카오페이에서 없으면 null.
    receipt_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
