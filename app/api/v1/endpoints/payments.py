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
from dataclasses import replace
from datetime import datetime, timedelta
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
    PortOneGateway,
    TossPaymentsGateway,
)

_log = logging.getLogger("catchap.payments")
router = APIRouter(tags=["payments"])

PaymentProvider = Literal["toss", "kakaopay", "portone", "mock"]


def _available_providers() -> list[str]:
    settings = get_settings()
    providers: list[str] = []
    if settings.toss_enabled:
        providers.append("toss")
    if settings.kakaopay_enabled:
        providers.append("kakaopay")
    if settings.portone_enabled:
        providers.append("portone")
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
    # 포트원 브라우저 SDK 초기화용 공개값. 미설정이면 빈 문자열.
    portone_store_id: str
    portone_channel_key: str
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
        portone_store_id=settings.PORTONE_STORE_ID if settings.portone_enabled else "",
        portone_channel_key=settings.PORTONE_CHANNEL_KEY if settings.portone_enabled else "",
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
    portone_store_id: str
    portone_channel_key: str
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
        portone_store_id=settings.PORTONE_STORE_ID if provider == "portone" else "",
        portone_channel_key=settings.PORTONE_CHANNEL_KEY if provider == "portone" else "",
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
    """토스 승인 / 포트원 조회 검증 / 개발용 mock 승인. 카카오페이는 approve 리다이렉트가 승인한다."""
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
        elif order.provider == "portone":
            if not settings.portone_enabled:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="포트원 설정이 완료되지 않았어요.",
                )
            # 포트원은 SDK가 결제창을 띄우고 서버는 조회로 검증한다(승인 요청 없음).
            # paymentId = 우리 order_uid 이므로 프런트가 보낸 값을 쓰지 않는다 — 위조 차단.
            payment = PortOneGateway(
                settings.PORTONE_API_SECRET, store_id=settings.PORTONE_STORE_ID
            ).verify(order.order_uid)
            if payment.status != PortOneGateway.PAID:
                raise PaymentGatewayError(
                    f"결제가 완료되지 않았어요(상태: {payment.status}).",
                    provider_code=payment.status,
                    # READY/PENDING 은 아직 진행 중일 수 있어 주문을 실패로 끊지 않는다.
                    uncertain=payment.status in ("READY", "PENDING", "VIRTUAL_ACCOUNT_ISSUED"),
                )
            if payment.amount != order.amount:
                raise PaymentGatewayError("결제 금액이 주문과 일치하지 않아요.")
            # _mark_paid 는 PG 무관하게 status=="DONE" 을 성공 규약으로 쓴다(카카오도 동일).
            # 포트원의 PAID 를 그 규약으로 정규화한다.
            payment = replace(payment, status="DONE")
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


# ---- 환불 정책 ----
# 전자상거래법상 디지털 콘텐츠는 7일 이내 청약철회가 원칙이되, 제공이 개시된 경우 제한할 수
# 있다(콘텐츠이용자보호지침). 강의는 시청 시작을 제공 개시로 본다. 여기에 학원법 별표4식
# '수강 진행률에 따른 비율 환불'을 얹는다(사용자 결정 2026-08-05):
#   · 미시청(1분 미만) + 7일 이내 → 전액(100%)
#   · 진행률 1/3 미만 → 2/3 환불 / 진행률 1/3~1/2 → 1/2 환불 / 1/2 초과 → 환불 불가
#   · 수료증 발급자 → 환불 불가(진행률 무관) / 결제 7일 초과 → 환불 불가
# 진행률 = 완료(done) 강의 수 / 전체 활성 강의 수. 부분 환불은 게이트웨이 cancelAmount로
# 집행하고(TossPaymentsGateway.cancel), mock 결제는 논리적으로 처리한다.
REFUND_WINDOW_DAYS = 7
# 재생을 아주 잠깐 건드린 것까지 '수강 시작'으로 보면 실수 클릭·미리보기로 환불이 막혀
# 문의가 늘어난다. 1분 미만은 시청하지 않은 것으로 본다.
REFUND_WATCHED_GRACE_SEC = 60
REFUND_TIER_1_RATIO = 2 / 3  # 진행률 1/3 미만
REFUND_TIER_2_RATIO = 1 / 2  # 진행률 1/3~1/2


def _refund_quote(db: Session, order: CourseOrder) -> dict:
    """환불 견적 — {refundable, blocked, deadline, ratio, amount, progress}.

    blocked 코드(not_paid | window_over | completed | progress_over)는 프런트가 문구를
    고르는 데 쓴다(서버 문장을 박아 두면 화면마다 말투가 갈린다). amount=실제 환불 금액(원),
    ratio=비율(0~1), progress=수강 진행률(%). 실제 차단·집행은 cancel_payment가 이 견적을
    다시 계산해 수행한다(화면 표시는 안내용).
    """
    from app.models.course_exam import CourseCompletion
    from app.models.lecture import Lecture, LectureWatchProgress

    if order.status != "paid" or not order.payment_key:
        return {"refundable": False, "blocked": "not_paid", "deadline": None, "ratio": 0.0, "amount": 0, "progress": 0}

    # 진행률 = 완료 강의 / 전체 활성 강의
    total = (
        db.query(Lecture.id)
        .filter(Lecture.course_id == order.course_id, Lecture.status == "active")
        .count()
    )
    done = 0
    if total:
        done = (
            db.query(LectureWatchProgress.id)
            .join(Lecture, Lecture.id == LectureWatchProgress.lecture_id)
            .filter(
                LectureWatchProgress.student_id == order.student_id,
                Lecture.course_id == order.course_id,
                Lecture.status == "active",
                LectureWatchProgress.status == "done",
            )
            .count()
        )
    progress = (done / total) if total else 0.0
    progress_pct = round(progress * 100)

    def q(refundable, blocked, deadline, ratio, amount):
        return {"refundable": refundable, "blocked": blocked, "deadline": deadline,
                "ratio": ratio, "amount": amount, "progress": progress_pct}

    # 수료증 발급자 → 환불 불가(진행률 무관)
    cert = (
        db.query(CourseCompletion.id)
        .filter(
            CourseCompletion.student_id == order.student_id,
            CourseCompletion.course_id == order.course_id,
        )
        .first()
    )
    if cert:
        return q(False, "completed", None, 0.0, 0)

    deadline = order.paid_at + timedelta(days=REFUND_WINDOW_DAYS) if order.paid_at else None
    if deadline and datetime.now() > deadline:
        return q(False, "window_over", deadline, 0.0, 0)

    # 미시청(1분 미만) → 전액 환불
    watched = (
        db.query(LectureWatchProgress.id)
        .join(Lecture, Lecture.id == LectureWatchProgress.lecture_id)
        .filter(
            LectureWatchProgress.student_id == order.student_id,
            Lecture.course_id == order.course_id,
            LectureWatchProgress.watched_max_sec >= REFUND_WATCHED_GRACE_SEC,
        )
        .first()
    )
    if not watched:
        return q(True, None, deadline, 1.0, order.amount)

    # 진행률 구간별 비율(학원법 별표4식)
    if progress < 1 / 3:
        ratio = REFUND_TIER_1_RATIO
    elif progress < 1 / 2:
        ratio = REFUND_TIER_2_RATIO
    else:
        return q(False, "progress_over", deadline, 0.0, 0)
    return q(True, None, deadline, ratio, int(order.amount * ratio))


class MyOrderOut(OrderOut):
    """주문 내역 한 줄 — 목록 화면에 필요한 맥락을 붙인다."""

    course_title: str
    # 취소 가능 여부 — 서버 정책(_refund_quote)의 결과를 그대로 담는다. 프런트가 status·날짜를
    # 보고 자기 나름대로 판단하면 정책이 바뀔 때 화면만 어긋난다. 실제 차단도 서버가 한다.
    refundable: bool
    # 불가 사유 코드(not_paid | window_over | completed | progress_over). 문구는 프런트가 고른다.
    refund_blocked: str | None = None
    # 환불 기한 — 남은 시간을 보여주기 위한 값
    refund_deadline: datetime | None = None
    # 진행률 기반 비율 환불 안내값 — 학생이 누르기 전에 얼마 돌려받는지 보여준다.
    refund_amount: int = 0  # 환불 예정 금액(원) — 부분 환불이면 결제액보다 작다
    refund_ratio: float = 0.0  # 환불 비율(0~1)
    refund_progress: int = 0  # 수강 진행률(%)
    # 환불하면 잃는 것 — 학생이 누르기 전에 알아야 한다.
    enrolled: bool
    completed: bool


@router.get("/payments/orders", response_model=list[MyOrderOut])
def my_orders(
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    """내 결제 내역. 최근 결제 순.

    라우트 순서 주의 — 아래 /payments/{order_uid} 보다 먼저 등록되어야 한다.
    뒤에 두면 order_uid="orders" 로 잡혀 404가 난다.

    pending/failed 는 학생이 할 수 있는 일이 없어 목록에서 뺀다(결제 화면에서 다시 시도하면
    서버가 살아있는 pending 주문을 재사용한다). 취소·환불된 건은 기록으로 남긴다.
    """
    from app.models import Course, CourseCompletion, CourseEnrollment

    rows = (
        db.query(CourseOrder, Course.title)
        .outerjoin(Course, Course.id == CourseOrder.course_id)
        .filter(
            CourseOrder.student_id == principal.id,
            CourseOrder.status.in_(("paid", "cancelled", "refunded", "partially_refunded")),
        )
        .order_by(CourseOrder.paid_at.desc(), CourseOrder.created_at.desc())
        .all()
    )
    if not rows:
        return []

    course_ids = {o.course_id for o, _ in rows}
    active = {
        r[0]
        for r in db.query(CourseEnrollment.course_id).filter(
            CourseEnrollment.student_id == principal.id,
            CourseEnrollment.course_id.in_(course_ids),
            CourseEnrollment.status == "active",
        )
    }
    done = {
        r[0]
        for r in db.query(CourseCompletion.course_id).filter(
            CourseCompletion.student_id == principal.id,
            CourseCompletion.course_id.in_(course_ids),
        )
    }
    def _row(o: CourseOrder, title: str | None) -> MyOrderOut:
        quote = _refund_quote(db, o)
        return MyOrderOut(
            **OrderOut.model_validate(o, from_attributes=True).model_dump(),
            # 코스가 삭제돼도 결제 기록은 남는다 — 제목이 비면 화면이 빈칸이 되므로 대체 문구를 준다.
            course_title=title or "(삭제된 코스)",
            refundable=quote["refundable"],
            refund_blocked=quote["blocked"],
            refund_deadline=quote["deadline"],
            refund_amount=quote["amount"],
            refund_ratio=quote["ratio"],
            refund_progress=quote["progress"],
            enrolled=o.course_id in active,
            completed=o.course_id in done,
        )

    return [_row(o, title) for o, title in rows]


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
    """본인 결제 취소·환불. 진행률 기반 비율 환불(부분/전액)을 계산해, PG 취소 성공 후에만
    로컬 주문·수강권에 반영하고 그 코스의 학습 이력·풀이 데이터를 삭제한다."""
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
    if order.status in ("cancelled", "refunded", "partially_refunded"):
        return OrderOut.model_validate(order, from_attributes=True)
    if order.status != "paid" or not order.payment_key:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="취소할 수 있는 결제가 아니에요.")

    # 환불 정책은 여기가 실제 게이트다(화면 표시는 안내용) — 견적을 서버에서 다시 계산한다.
    quote = _refund_quote(db, order)
    if not quote["refundable"]:
        blocked = quote["blocked"]
        message = {
            "window_over": f"환불 가능 기간({REFUND_WINDOW_DAYS}일)이 지났어요.",
            "completed": "수료증이 발급된 코스는 환불되지 않아요.",
            "progress_over": "수강 진행률이 50%를 넘어 환불되지 않아요.",
            "not_paid": "취소할 수 있는 결제가 아니에요.",
        }.get(blocked, "환불할 수 없는 주문이에요.")
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={
                "message": message,
                "reason": blocked,
                "refund_deadline": quote["deadline"].isoformat() if quote["deadline"] else None,
                "help": "자세한 사항은 고객 지원으로 문의해 주세요.",
            },
        )
    # 부분 환불이면 결제액보다 작다. 게이트웨이에는 이 금액으로 취소를 요청한다.
    refund_amount = int(quote["amount"])
    partial = refund_amount < order.amount

    settings = get_settings()
    try:
        if order.provider == "toss":
            if not settings.toss_enabled:
                raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="토스 설정이 없어요.")
            TossPaymentsGateway(settings.TOSS_SECRET_KEY).cancel(
                order.payment_key,
                reason=body.reason,
                idempotency_key=f"cancel-{order.order_uid}",
                cancel_amount=refund_amount if partial else None,
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
            ).cancel(order.payment_key, amount=refund_amount)
        elif order.provider == "portone":
            if not settings.portone_enabled:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE, detail="포트원 설정이 없어요."
                )
            # 취소 대상은 우리 order_uid(= 포트원 paymentId). 취소 후 재조회로 반영을 확인한다.
            PortOneGateway(
                settings.PORTONE_API_SECRET, store_id=settings.PORTONE_STORE_ID
            ).cancel(order.order_uid, amount=refund_amount, reason=body.reason)
        elif order.provider == "mock" and settings.payment_mock_enabled:
            pass
        else:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, detail="결제 취소 설정이 없어요."
            )
    except PaymentGatewayError as exc:
        raise _provider_failure(exc)

    order.status = "refunded" if refund_amount >= order.amount else "partially_refunded"
    order.cancelled_at = datetime.now()
    # 부분 환불이면 얼마 돌려줬는지 사유에 남긴다(주문 모델에 환불액 컬럼이 없어 감사 기록 용도).
    order.cancel_reason = f"{body.reason} [환불 {refund_amount:,}원/{order.amount:,}원]"[:200]
    _revoke_enrollment_if_unpaid_elsewhere(db, order)
    # 수강 취소·환불 시 그 코스의 학습 이력·풀이 데이터 삭제(사용자 결정 2026-08-05).
    from app.services.enrollment_lifecycle import purge_course_learning_data

    purge_course_learning_data(db, order.student_id, order.course_id)
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


class PortOneWebhookIn(BaseModel):
    type: str
    data: dict


@router.post("/payments/webhooks/portone")
def portone_webhook(
    payload: PortOneWebhookIn,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """포트원 결제 상태 웹훅.

    포트원 문서가 명시하는 원칙 그대로 — 웹훅 본문을 신뢰하지 않고 paymentId 로 결제 건을
    다시 조회해 그 응답만 반영한다(위조 요청 방어). 서명 검증은 시크릿이 설정된 경우에만
    추가 방어로 쓰고, 검증을 통과했더라도 재조회 결과가 정본이다.
    """
    client_ip = request.client.host if request.client else "unknown"
    auth_service.rate_limit(
        db, f"payment-webhook:portone:{client_ip}", limit=120, window_seconds=60
    )
    payment_id = str(payload.data.get("paymentId") or "")
    if not payment_id or len(payment_id) > 200:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="paymentId가 없어요.")
    settings = get_settings()
    if not settings.portone_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="포트원 설정이 없어요.")
    try:
        payment = PortOneGateway(
            settings.PORTONE_API_SECRET, store_id=settings.PORTONE_STORE_ID
        ).verify(payment_id)
    except PaymentGatewayError as exc:
        raise _provider_failure(exc)

    order = (
        db.query(CourseOrder)
        .filter(CourseOrder.order_uid == payment_id, CourseOrder.provider == "portone")
        .with_for_update()
        .first()
    )
    if order is None:
        # 우리 주문이 아닌 결제는 성공 응답으로 버린다(재전송해도 복구할 수 없다).
        _log.warning("알 수 없는 포트원 웹훅 payment=%s", payment_id)
        return {"ok": True, "ignored": True}
    if payment.amount != order.amount:
        _log.error(
            "포트원 웹훅 금액 불일치 order=%s expected=%s actual=%s",
            order.order_uid,
            order.amount,
            payment.amount,
        )
        raise HTTPException(status.HTTP_409_CONFLICT, detail="결제 금액이 주문과 일치하지 않아요.")

    if payment.status == PortOneGateway.PAID and order.status == "pending":
        # confirm 경로와 같은 규약 — _mark_paid 는 status=="DONE" 만 성공으로 본다.
        newly_active = _mark_paid(db, order, replace(payment, status="DONE"))
        if newly_active:
            background_tasks.add_task(_notify_enroll, order.student_id, order.course_id)
    elif payment.status in PortOneGateway.CANCELLED and order.status == "paid":
        order.status = "refunded"
        order.cancelled_at = datetime.now()
        order.cancel_reason = order.cancel_reason or "포트원 취소 웹훅"
        _revoke_enrollment_if_unpaid_elsewhere(db, order)
        db.commit()
    elif payment.status == "FAILED" and order.status == "pending":
        order.status = "failed"
        order.fail_reason = "포트원 결제 실패"
        db.commit()
    return {"ok": True}
