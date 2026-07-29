"""코스 단건 결제 — 카카오페이 QR 간편결제 + 토스페이먼츠.

공통 흐름:
  1) 서버 가격으로 pending 주문 생성
  2) 토스 SDK 인증 또는 카카오페이 ready(PC QR)
  3) 서버가 PG 승인 응답의 주문번호·금액·상태를 재검증
  4) paid 주문과 active 수강권을 함께 저장
  5) 취소 시 PG 전액 취소 후 수강권 회수

비밀 키는 전부 서버 환경변수에서만 읽는다. 운영 환경에서는 키가 없을 때 mock 성공으로
폴백하지 않는다. 카드번호·CVC·생년월일 등 결제수단 원문은 API로 받거나 DB에 저장하지 않는다.
"""

from __future__ import annotations

import hmac
import logging
from datetime import datetime
from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.endpoints.lectures import _notify_enroll
from app.core.config import get_settings
from app.core.permissions import Principal, require_student
from app.core.security import generate_token, new_uuid, sha256_hash
from app.db.session import get_db
from app.models import Course, CourseEnrollment, CourseOrder, Lecture, User
from app.services import auth_service
from app.services.course_pricing import effective_course_price
from app.services.payment_gateways import (
    ApprovedPayment,
    KakaoPayGateway,
    PaymentGatewayError,
    TossPaymentsGateway,
)

_log = logging.getLogger("catchap.payments")
router = APIRouter(tags=["payments"])

PaymentProvider = Literal["toss", "kakaopay", "mock"]


def _available_providers() -> list[str]:
    settings = get_settings()
    providers: list[str] = []
    if settings.toss_enabled:
        providers.append("toss")
    if settings.kakaopay_enabled:
        providers.append("kakaopay")
    if settings.payment_mock_enabled:
        providers.append("mock")
    return providers


def _resolve_provider(requested: PaymentProvider | None) -> str:
    available = _available_providers()
    if requested is not None:
        if requested not in available:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "reason": "payment_provider_unavailable",
                    "message": f"{requested} 결제 설정이 완료되지 않았어요.",
                    "available_providers": available,
                },
            )
        return requested
    if available:
        return available[0]
    raise HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "reason": "payment_not_configured",
            "message": "사용 가능한 결제수단이 아직 설정되지 않았어요.",
            "available_providers": [],
        },
    )


def _load_active_course(db: Session, course_id: str) -> Course:
    course = db.get(Course, course_id)
    if course is None or course.status != "active":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="코스를 찾을 수 없어요.")
    return course


def _is_enrolled(db: Session, student_id: str, course_id: str) -> bool:
    return (
        db.query(CourseEnrollment.id)
        .filter(
            CourseEnrollment.student_id == student_id,
            CourseEnrollment.course_id == course_id,
            CourseEnrollment.status == "active",
        )
        .first()
        is not None
    )


def _activate_enrollment(db: Session, student_id: str, course_id: str) -> bool:
    """현재 트랜잭션 안에서 수강권을 활성화하고 새 활성화 여부를 반환한다."""
    enrollment = (
        db.query(CourseEnrollment)
        .filter(
            CourseEnrollment.student_id == student_id,
            CourseEnrollment.course_id == course_id,
        )
        .first()
    )
    was_active = enrollment is not None and enrollment.status == "active"
    if enrollment is None:
        db.add(
            CourseEnrollment(
                student_id=student_id,
                course_id=course_id,
                status="active",
                enrolled_at=datetime.now(),
            )
        )
    else:
        enrollment.status = "active"
        enrollment.enrolled_at = datetime.now()
    return not was_active


def _mark_paid(
    db: Session,
    order: CourseOrder,
    payment: ApprovedPayment,
    *,
    method: str | None = None,
) -> bool:
    """검증된 PG 결과를 주문·수강권에 한 트랜잭션으로 반영한다."""
    if payment.order_id and payment.order_id != order.order_uid:
        raise PaymentGatewayError("결제 승인 주문번호가 서버 주문과 일치하지 않아요.")
    if payment.amount != order.amount or payment.status != "DONE":
        raise PaymentGatewayError("결제 승인 금액 또는 상태가 서버 주문과 일치하지 않아요.")

    def apply_order_fields(target: CourseOrder) -> None:
        target.payment_key = payment.provider_payment_id
        target.method = payment.method or method
        target.receipt_url = payment.receipt_url
        target.status = "paid"
        target.paid_at = datetime.now()
        target.fail_reason = None
        target.callback_token_hash = None

    order_id = order.id
    student_id = order.student_id
    course_id = order.course_id
    apply_order_fields(order)
    newly_active = _activate_enrollment(db, student_id, course_id)
    try:
        db.commit()
    except IntegrityError:
        # 다른 PG/탭의 동시 콜백이 수강권 UNIQUE(student_id, course_id)를 먼저 만들었을 수
        # 있다. 외부 결제는 이미 승인됐으므로 500으로 끝내지 말고 기존 수강권에 합류한다.
        db.rollback()
        saved_order = db.get(CourseOrder, order_id)
        existing = (
            db.query(CourseEnrollment)
            .filter(
                CourseEnrollment.student_id == student_id,
                CourseEnrollment.course_id == course_id,
            )
            .first()
        )
        if saved_order is None or existing is None:
            raise
        apply_order_fields(saved_order)
        existing.status = "active"
        existing.enrolled_at = datetime.now()
        db.commit()
        return False
    return newly_active


def _revoke_enrollment_if_unpaid_elsewhere(db: Session, order: CourseOrder) -> None:
    """같은 코스의 다른 유효 결제가 없을 때만 수강권을 회수한다."""
    other_paid = (
        db.query(CourseOrder.id)
        .filter(
            CourseOrder.student_id == order.student_id,
            CourseOrder.course_id == order.course_id,
            CourseOrder.id != order.id,
            CourseOrder.status == "paid",
        )
        .first()
    )
    if other_paid is not None:
        return
    enrollment = (
        db.query(CourseEnrollment)
        .filter(
            CourseEnrollment.student_id == order.student_id,
            CourseEnrollment.course_id == order.course_id,
            CourseEnrollment.status == "active",
        )
        .first()
    )
    if enrollment is not None:
        enrollment.status = "withdrawn"


def _append_query(url: str, **params: str) -> str:
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode(params)}"


def _provider_failure(exc: PaymentGatewayError) -> HTTPException:
    _log.warning("PG 요청 실패 code=%s message=%s", exc.provider_code, exc.message)
    return HTTPException(
        status.HTTP_502_BAD_GATEWAY,
        detail={
            "reason": "payment_gateway_error",
            "message": exc.message,
            "provider_code": exc.provider_code,
        },
    )


class CheckoutOut(BaseModel):
    course_id: str
    course_title: str
    instructor_name: str | None
    lecture_count: int
    amount: int
    already_enrolled: bool
    provider: str
    available_providers: list[str]
    toss_client_key: str
    customer_key: str


@router.get("/courses/{course_id}/checkout", response_model=CheckoutOut)
def checkout_info(
    course_id: str,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    course = _load_active_course(db, course_id)
    lecture_count = (
        db.query(Lecture.id)
        .filter(Lecture.course_id == course.id, Lecture.status == "active")
        .count()
    )
    instructor = db.get(User, course.instructor_id) if course.instructor_id else None
    settings = get_settings()
    providers = _available_providers()
    return CheckoutOut(
        course_id=course.id,
        course_title=course.title,
        instructor_name=instructor.name if instructor else None,
        lecture_count=lecture_count,
        amount=effective_course_price(course),
        already_enrolled=_is_enrolled(db, principal.id, course.id),
        provider=providers[0] if providers else "unavailable",
        available_providers=providers,
        toss_client_key=settings.TOSS_CLIENT_KEY if settings.toss_enabled else "",
        # 학생 PK는 서버가 만든 UUID라 이메일·전화번호 같은 PII가 아니며 추측하기 어렵다.
        customer_key=f"catchap_{principal.id}",
    )


class CreateOrderIn(BaseModel):
    course_id: str
    provider: PaymentProvider | None = None


class CreateOrderOut(BaseModel):
    order_uid: str
    amount: int
    provider: str
    available_providers: list[str]
    course_title: str
    toss_client_key: str
    customer_key: str


@router.post("/payments/checkout", response_model=CreateOrderOut)
def create_order(
    body: CreateOrderIn,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    """서버 가격으로 주문 생성.

    학생·코스당 살아있는 pending 주문은 하나만 둔다. 결제수단을 바꾸면 이전 pending을
    취소하고 새 주문을 만들어 두 PG를 동시에 승인하는 이중 결제를 막는다.
    """
    course = _load_active_course(db, body.course_id)
    if _is_enrolled(db, principal.id, course.id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"reason": "already_enrolled", "message": "이미 수강 중인 코스예요."},
        )
    amount = effective_course_price(course)
    if amount <= 0:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={
                "reason": "free_course",
                "message": "무료 코스는 결제 없이 수강신청해 주세요.",
            },
        )
    provider = _resolve_provider(body.provider)
    order = (
        db.query(CourseOrder)
        .filter(
            CourseOrder.student_id == principal.id,
            CourseOrder.course_id == course.id,
            CourseOrder.status == "pending",
        )
        .order_by(CourseOrder.created_at.desc())
        .with_for_update()
        .first()
    )
    if order is not None:
        age_sec = (datetime.now() - order.created_at).total_seconds() if order.created_at else 9999
        if age_sec >= 30 * 60 or order.provider != provider:
            order.status = "cancelled"
            order.cancelled_at = datetime.now()
            order.cancel_reason = (
                "주문 유효시간 만료" if age_sec >= 30 * 60 else "결제수단 변경"
            )
            db.flush()
            order = None
    if order is None:
        order = CourseOrder(
            student_id=principal.id,
            course_id=course.id,
            order_uid=f"catchap_{new_uuid().replace('-', '')}",
            amount=amount,
            status="pending",
            provider=provider,
        )
        db.add(order)
    # 기존 pending 주문을 재사용할 때 금액은 바꾸지 않는다. 주문 금액은 생성 시점
    # 스냅샷이며, 인증이 시작된 뒤 가격을 바꾸면 승인 금액 불일치와 결제 후 미지급이 생긴다.
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="주문 생성이 겹쳤어요. 다시 시도해 주세요."
        )
    settings = get_settings()
    return CreateOrderOut(
        order_uid=order.order_uid,
        amount=order.amount,
        provider=order.provider,
        available_providers=_available_providers(),
        course_title=course.title,
        toss_client_key=settings.TOSS_CLIENT_KEY if provider == "toss" else "",
        customer_key=f"catchap_{principal.id}",
    )


class KakaoReadyIn(BaseModel):
    order_uid: str


class KakaoReadyOut(BaseModel):
    order_uid: str
    amount: int
    tid: str
    next_redirect_pc_url: str
    next_redirect_mobile_url: str
    next_redirect_app_url: str


@router.post("/payments/kakaopay/ready", response_model=KakaoReadyOut)
def kakaopay_ready(
    body: KakaoReadyIn,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    """카카오페이 결제 준비. PC URL을 열면 카카오페이가 QR을 표시한다."""
    order = (
        db.query(CourseOrder)
        .filter(
            CourseOrder.order_uid == body.order_uid,
            CourseOrder.student_id == principal.id,
        )
        .first()
    )
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="주문을 찾을 수 없어요.")
    if order.provider != "kakaopay" or order.status != "pending":
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="카카오페이 결제를 준비할 수 없는 주문이에요."
        )
    settings = get_settings()
    if not settings.kakaopay_enabled:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="카카오페이 설정이 완료되지 않았어요."
        )

    # 16분 안의 ready 결과는 재사용한다. 새 ready로 기존 QR의 callback state를 무효화하면
    # 사용자가 이전 QR로 결제한 뒤 수강권을 못 받는 문제가 생기므로 중복 세션을 만들지 않는다.
    session = order.provider_session if isinstance(order.provider_session, dict) else {}
    age_sec = (datetime.now() - order.updated_at).total_seconds() if order.updated_at else 9999
    if (
        order.payment_key
        and order.callback_token_hash
        and age_sec < 16 * 60
        and session.get("next_redirect_pc_url")
    ):
        return KakaoReadyOut(
            order_uid=order.order_uid,
            amount=order.amount,
            tid=order.payment_key,
            next_redirect_pc_url=session["next_redirect_pc_url"],
            next_redirect_mobile_url=session.get("next_redirect_mobile_url", ""),
            next_redirect_app_url=session.get("next_redirect_app_url", ""),
        )

    course = _load_active_course(db, order.course_id)
    state_token = generate_token()
    callback_query = {"order_uid": order.order_uid, "state": state_token}
    callback_base = f"{settings.BACKEND_URL.rstrip('/')}/api/v1/payments/kakaopay"
    try:
        ready = KakaoPayGateway(
            settings.KAKAOPAY_CID,
            settings.KAKAOPAY_SECRET_KEY,
            cid_secret=settings.KAKAOPAY_CID_SECRET,
        ).ready(
            order_id=order.order_uid,
            user_id=order.student_id,
            item_name=course.title,
            amount=order.amount,
            approval_url=f"{callback_base}/approve?{urlencode(callback_query)}",
            cancel_url=f"{callback_base}/cancel?{urlencode(callback_query)}",
            fail_url=f"{callback_base}/fail?{urlencode(callback_query)}",
        )
    except PaymentGatewayError as exc:
        order.fail_reason = exc.message[:200]
        db.commit()
        raise _provider_failure(exc)
    order.payment_key = ready.tid
    order.callback_token_hash = sha256_hash(state_token)
    order.provider_session = {
        "next_redirect_pc_url": ready.next_redirect_pc_url,
        "next_redirect_mobile_url": ready.next_redirect_mobile_url,
        "next_redirect_app_url": ready.next_redirect_app_url,
    }
    order.fail_reason = None
    db.commit()
    return KakaoReadyOut(order_uid=order.order_uid, amount=order.amount, **ready.__dict__)


def _kakao_callback_order(
    db: Session, order_uid: str, state_token: str
) -> CourseOrder:
    order = (
        db.query(CourseOrder)
        .filter(CourseOrder.order_uid == order_uid, CourseOrder.provider == "kakaopay")
        .with_for_update()
        .first()
    )
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="주문을 찾을 수 없어요.")
    if order.status == "paid":
        return order
    expected = order.callback_token_hash or ""
    if not expected or not hmac.compare_digest(expected, sha256_hash(state_token)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="결제 콜백 정보가 올바르지 않아요.")
    return order


@router.get("/payments/kakaopay/approve")
def kakaopay_approve(
    order_uid: str,
    state: str,
    pg_token: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """카카오페이 인증 완료 리다이렉트 → 서버 승인 → 수강권 활성화 → 프런트 성공 화면."""
    order = _kakao_callback_order(db, order_uid, state)
    settings = get_settings()
    if order.status == "paid":
        return RedirectResponse(
            _append_query(settings.payment_success_url, orderId=order.order_uid), status_code=303
        )
    if order.status != "pending" or not order.payment_key:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="승인할 수 없는 주문이에요.")
    try:
        payment = KakaoPayGateway(
            settings.KAKAOPAY_CID,
            settings.KAKAOPAY_SECRET_KEY,
            cid_secret=settings.KAKAOPAY_CID_SECRET,
        ).approve(
            tid=order.payment_key,
            order_id=order.order_uid,
            user_id=order.student_id,
            pg_token=pg_token,
            amount=order.amount,
        )
        newly_active = _mark_paid(db, order, payment)
    except PaymentGatewayError as exc:
        if not exc.uncertain:
            order.status = "failed"
        order.fail_reason = exc.message[:200]
        if not exc.uncertain:
            order.callback_token_hash = None
        db.commit()
        _log.warning(
            "카카오페이 승인 실패 order=%s code=%s", order.order_uid, exc.provider_code
        )
        return RedirectResponse(
            _append_query(
                settings.payment_fail_url,
                orderId=order.order_uid,
                reason=(
                    "payment_status_unknown"
                    if exc.uncertain
                    else "payment_gateway_error"
                ),
            ),
            status_code=303,
        )
    if newly_active:
        background_tasks.add_task(_notify_enroll, order.student_id, order.course_id)
    return RedirectResponse(
        _append_query(settings.payment_success_url, orderId=order.order_uid), status_code=303
    )


def _finish_kakao_redirect(
    db: Session, order_uid: str, state_token: str, *, failed: bool
) -> RedirectResponse:
    order = _kakao_callback_order(db, order_uid, state_token)
    settings = get_settings()
    if order.status == "paid":
        return RedirectResponse(
            _append_query(settings.payment_success_url, orderId=order.order_uid),
            status_code=303,
        )
    if order.status == "pending":
        order.status = "failed" if failed else "cancelled"
        order.fail_reason = "사용자 취소" if not failed else "카카오페이 인증 실패"
        order.callback_token_hash = None
        order.cancelled_at = datetime.now() if not failed else None
        db.commit()
    target = settings.payment_fail_url if failed else settings.payment_cancel_url
    return RedirectResponse(_append_query(target, orderId=order.order_uid), status_code=303)


@router.get("/payments/kakaopay/cancel")
def kakaopay_cancel_redirect(
    order_uid: str, state: str, db: Session = Depends(get_db)
):
    return _finish_kakao_redirect(db, order_uid, state, failed=False)


@router.get("/payments/kakaopay/fail")
def kakaopay_fail_redirect(
    order_uid: str, state: str, db: Session = Depends(get_db)
):
    return _finish_kakao_redirect(db, order_uid, state, failed=True)


class ConfirmIn(BaseModel):
    order_uid: str
    amount: int = Field(ge=0)
    payment_key: str | None = Field(default=None, max_length=200)
    method: str | None = Field(default=None, max_length=30)


class ConfirmOut(BaseModel):
    ok: bool
    enrolled: bool
    course_id: str
    order_uid: str
    amount: int
    provider: str
    method: str | None
    receipt_url: str | None


@router.post("/payments/confirm", response_model=ConfirmOut)
def confirm_payment(
    body: ConfirmIn,
    background_tasks: BackgroundTasks,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    """토스 승인 또는 개발용 mock 승인. 카카오페이는 approve 리다이렉트가 승인한다."""
    order = (
        db.query(CourseOrder)
        .filter(
            CourseOrder.order_uid == body.order_uid,
            CourseOrder.student_id == principal.id,
        )
        .with_for_update()
        .first()
    )
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="주문을 찾을 수 없어요.")
    if order.status == "paid":
        return ConfirmOut(
            ok=True,
            enrolled=True,
            course_id=order.course_id,
            order_uid=order.order_uid,
            amount=order.amount,
            provider=order.provider,
            method=order.method,
            receipt_url=order.receipt_url,
        )
    if order.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="이미 종료된 주문이에요.")
    if body.amount != order.amount:
        order.status = "failed"
        order.fail_reason = "금액 불일치"
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="결제 금액이 올바르지 않아요.")
    _load_active_course(db, order.course_id)

    settings = get_settings()
    try:
        if order.provider == "toss":
            if not settings.toss_enabled:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="토스페이먼츠 설정이 완료되지 않았어요.",
                )
            if not body.payment_key:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="paymentKey가 없어요.")
            payment = TossPaymentsGateway(settings.TOSS_SECRET_KEY).confirm(
                body.payment_key, order.order_uid, order.amount
            )
        elif order.provider == "mock":
            if not settings.payment_mock_enabled:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="운영 환경에서는 모의 결제를 사용할 수 없어요.",
                )
            payment = ApprovedPayment(
                provider_payment_id=f"mock_{order.order_uid}",
                order_id=order.order_uid,
                amount=order.amount,
                status="DONE",
                method=body.method or "모의결제",
            )
        else:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="카카오페이는 QR 인증 완료 후 자동으로 승인됩니다.",
            )
        newly_active = _mark_paid(db, order, payment, method=body.method)
    except PaymentGatewayError as exc:
        if not exc.uncertain:
            order.status = "failed"
        order.fail_reason = exc.message[:200]
        db.commit()
        raise _provider_failure(exc)
    if newly_active:
        background_tasks.add_task(_notify_enroll, principal.id, order.course_id)
    return ConfirmOut(
        ok=True,
        enrolled=True,
        course_id=order.course_id,
        order_uid=order.order_uid,
        amount=order.amount,
        provider=order.provider,
        method=order.method,
        receipt_url=order.receipt_url,
    )


class OrderOut(BaseModel):
    order_uid: str
    course_id: str
    amount: int
    status: str
    provider: str
    method: str | None
    receipt_url: str | None
    paid_at: datetime | None
    cancelled_at: datetime | None
    fail_reason: str | None


@router.get("/payments/{order_uid}", response_model=OrderOut)
def payment_status(
    order_uid: str,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    order = (
        db.query(CourseOrder)
        .filter(
            CourseOrder.order_uid == order_uid,
            CourseOrder.student_id == principal.id,
        )
        .first()
    )
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="주문을 찾을 수 없어요.")
    return OrderOut.model_validate(order, from_attributes=True)


class CancelPaymentIn(BaseModel):
    reason: str = Field(min_length=1, max_length=200)


@router.post("/payments/{order_uid}/cancel", response_model=OrderOut)
def cancel_payment(
    order_uid: str,
    body: CancelPaymentIn,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    """본인 결제 전액 취소. PG 취소 성공 후에만 로컬 주문과 수강권을 변경한다."""
    order = (
        db.query(CourseOrder)
        .filter(
            CourseOrder.order_uid == order_uid,
            CourseOrder.student_id == principal.id,
        )
        .with_for_update()
        .first()
    )
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="주문을 찾을 수 없어요.")
    if order.status in ("cancelled", "refunded"):
        return OrderOut.model_validate(order, from_attributes=True)
    if order.status != "paid" or not order.payment_key:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="취소할 수 있는 결제가 아니에요.")

    settings = get_settings()
    try:
        if order.provider == "toss":
            if not settings.toss_enabled:
                raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="토스 설정이 없어요.")
            TossPaymentsGateway(settings.TOSS_SECRET_KEY).cancel(
                order.payment_key,
                reason=body.reason,
                idempotency_key=f"cancel-{order.order_uid}",
            )
        elif order.provider == "kakaopay":
            if not settings.kakaopay_enabled:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE, detail="카카오페이 설정이 없어요."
                )
            KakaoPayGateway(
                settings.KAKAOPAY_CID,
                settings.KAKAOPAY_SECRET_KEY,
                cid_secret=settings.KAKAOPAY_CID_SECRET,
            ).cancel(order.payment_key, amount=order.amount)
        elif order.provider == "mock" and settings.payment_mock_enabled:
            pass
        else:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, detail="결제 취소 설정이 없어요."
            )
    except PaymentGatewayError as exc:
        raise _provider_failure(exc)

    order.status = "refunded"
    order.cancelled_at = datetime.now()
    order.cancel_reason = body.reason
    _revoke_enrollment_if_unpaid_elsewhere(db, order)
    db.commit()
    return OrderOut.model_validate(order, from_attributes=True)


class TossWebhookIn(BaseModel):
    eventType: str
    data: dict


@router.post("/payments/webhooks/toss")
def toss_webhook(
    payload: TossWebhookIn,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """토스 결제 상태 웹훅.

    PAYMENT_STATUS_CHANGED에는 일반 결제용 서명이 없으므로 전달된 data를 정본으로 믿지 않고,
    서버 시크릿 키로 토스 결제를 재조회한 결과만 반영한다.
    """
    client_ip = request.client.host if request.client else "unknown"
    auth_service.rate_limit(
        db, f"payment-webhook:toss:{client_ip}", limit=120, window_seconds=60
    )
    if payload.eventType not in ("PAYMENT_STATUS_CHANGED", "CANCEL_STATUS_CHANGED"):
        return {"ok": True, "ignored": True}
    payment_key = str(payload.data.get("paymentKey") or "")
    if not payment_key or len(payment_key) > 200:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="paymentKey가 없어요.")
    settings = get_settings()
    if not settings.toss_enabled:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="토스페이먼츠 설정이 없어요."
        )
    try:
        payment = TossPaymentsGateway(settings.TOSS_SECRET_KEY).fetch(payment_key)
    except PaymentGatewayError as exc:
        raise _provider_failure(exc)

    order = (
        db.query(CourseOrder)
        .filter(
            CourseOrder.order_uid == payment.order_id,
            CourseOrder.provider == "toss",
        )
        .with_for_update()
        .first()
    )
    if order is None:
        # 우리 주문이 아닌 토스 결제는 성공 응답으로 버린다. 재전송을 유발해도 복구할 수 없다.
        _log.warning("알 수 없는 토스 웹훅 order=%s", payment.order_id)
        return {"ok": True, "ignored": True}
    if payment.amount != order.amount:
        _log.error(
            "토스 웹훅 금액 불일치 order=%s expected=%s actual=%s",
            order.order_uid,
            order.amount,
            payment.amount,
        )
        raise HTTPException(status.HTTP_409_CONFLICT, detail="결제 금액이 주문과 일치하지 않아요.")

    if payment.status == "DONE" and order.status == "pending":
        newly_active = _mark_paid(db, order, payment)
        if newly_active:
            background_tasks.add_task(_notify_enroll, order.student_id, order.course_id)
    elif payment.status == "CANCELED" and order.status in ("paid", "partially_refunded"):
        order.status = "refunded"
        order.cancelled_at = datetime.now()
        order.cancel_reason = "토스페이먼츠 상태 동기화"
        _revoke_enrollment_if_unpaid_elsewhere(db, order)
        db.commit()
    elif payment.status == "PARTIAL_CANCELED" and order.status == "paid":
        # 이 API는 전액 취소만 요청하지만 토스 관리자에서 부분 취소될 수 있다.
        # 잔액 컬럼이 없으므로 수강권은 유지하고 상태만 별도로 표시해 운영자가 확인하게 한다.
        order.status = "partially_refunded"
        db.commit()
    elif payment.status in ("ABORTED", "EXPIRED") and order.status == "pending":
        order.status = "failed"
        order.fail_reason = f"토스 상태: {payment.status}"
        db.commit()
    return {"ok": True, "status": order.status}
