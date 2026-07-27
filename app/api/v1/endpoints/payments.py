"""코스 수강 결제 — 유료 코스 수강신청의 '주문 → 승인 → 확정' 흐름(토스페이먼츠).

  학생(require_student)
    GET  /courses/{id}/checkout   결제 화면 요약(코스명·강사·강의 수·금액·이미 수강 여부·결제경로)
    POST /payments/checkout       주문 생성 — 서버가 금액을 확정 저장하고 order_uid를 발급(pending).
                                  같은 코스에 살아있는 pending 주문이 있으면 그대로 재사용(중복 방지).
    POST /payments/confirm        결제 확정 — 클라이언트가 보낸 금액을 주문 금액과 대조(위변조 방어)한 뒤
                                  실제 토스 승인(키 있으면) 또는 mock 승인. 성공 시 수강신청을 active로.

설계 결정(사용자 2026-07-27): **실제 PG(토스) 연동 구조 + 데모 고정 가격.** 가격은 Course에
필드를 두지 않고 course_id로 결정적으로 산출한다(demo_course_price) — 스키마 변경 없이 결제 UX를
보여 주고, 나중에 Course.price를 붙이면 이 함수만 교체하면 된다. TOSS_SECRET_KEY가 있으면
서버 confirm이 실제 토스 승인 API를 호출하고, 없으면 mock 승인으로 폴백(로컬·데모).

금액 위변조 방어(PG 연동의 핵심 계약): 금액은 **주문 생성 시 서버가 확정**해 course_orders.amount에
저장하고, confirm 때 클라이언트가 보낸 amount와 대조한다. 실제 토스 승인도 이 서버 금액으로 호출한다.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.endpoints.lectures import _notify_enroll, activate_enrollment
from app.core.config import get_settings
from app.core.permissions import Principal, require_student
from app.core.security import new_uuid
from app.db.session import get_db
from app.models import Course, CourseEnrollment, CourseOrder, Lecture, User

_log = logging.getLogger("catchap.payments")
router = APIRouter(tags=["payments"])

# 데모 가격 티어(원) — course_id로 결정적으로 하나를 고른다. 무료(0) 없이 전부 유료 가정
# (결제 화면을 보여 주는 게 목적). 실제 서비스에선 Course.price로 대체.
_PRICE_TIERS = (33_000, 49_000, 66_000, 88_000, 99_000, 132_000)

_TOSS_CONFIRM_URL = "https://api.tosspayments.com/v1/payments/confirm"
_TOSS_TIMEOUT_SEC = 10.0


def demo_course_price(course: Course) -> int:
    """코스의 (데모) 수강료를 원 단위로 결정적으로 산출한다.

    Course에 가격 필드가 없어(사용자 결정: 데모 고정가) course_id 해시로 티어 하나를 고정 매핑한다.
    같은 코스는 항상 같은 금액이라 새로고침/재주문에도 금액이 흔들리지 않는다. 실제 유료화 시에는
    이 함수를 Course.price 조회로 바꾸면 나머지 결제 흐름은 그대로 동작한다."""
    h = sum(ord(ch) for ch in (course.id or ""))
    return _PRICE_TIERS[h % len(_PRICE_TIERS)]


def _pg_provider() -> str:
    return "toss" if get_settings().toss_enabled else "mock"


class CheckoutOut(BaseModel):
    course_id: str
    course_title: str
    instructor_name: str | None
    lecture_count: int
    amount: int
    already_enrolled: bool
    provider: str  # toss | mock
    # 토스 결제창 초기화용 공개 키(mock이면 빈 문자열) + 고객 식별 키(토스 위젯 요구)
    toss_client_key: str
    customer_key: str


def _load_active_course(db: Session, course_id: str) -> Course:
    c = db.get(Course, course_id)
    if c is None or c.status != "active":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="코스를 찾을 수 없어요.")
    return c


def _is_enrolled(db: Session, student_id: str, course_id: str) -> bool:
    return (
        db.query(CourseEnrollment)
        .filter(
            CourseEnrollment.student_id == student_id,
            CourseEnrollment.course_id == course_id,
            CourseEnrollment.status == "active",
        )
        .first()
        is not None
    )


@router.get("/courses/{course_id}/checkout", response_model=CheckoutOut)
def checkout_info(
    course_id: str,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    """결제 화면 요약 — 코스명·강사·강의 수·결제 금액·이미 수강 여부·결제 경로.

    이미 수강 중이면 already_enrolled=true를 주고(프런트가 '이미 수강 중' 안내로 분기),
    금액은 서버가 확정하는 값(demo_course_price)을 그대로 노출한다(주문 생성 시 금액과 동일)."""
    c = _load_active_course(db, course_id)
    lecture_count = (
        db.query(Lecture.id)
        .filter(Lecture.course_id == c.id, Lecture.status == "active")
        .count()
    )
    inst = db.get(User, c.instructor_id) if c.instructor_id else None
    s = get_settings()
    return CheckoutOut(
        course_id=c.id,
        course_title=c.title,
        instructor_name=inst.name if inst else None,
        lecture_count=lecture_count,
        amount=demo_course_price(c),
        already_enrolled=_is_enrolled(db, principal.id, c.id),
        provider=_pg_provider(),
        toss_client_key=s.TOSS_CLIENT_KEY if s.toss_enabled else "",
        # 토스 결제 위젯은 고객 식별 키를 요구한다 — 학생 id로 안정적으로 만든다(PII 아님).
        customer_key=f"catchap_{principal.id}",
    )


class CreateOrderIn(BaseModel):
    course_id: str


class CreateOrderOut(BaseModel):
    order_uid: str
    amount: int
    provider: str
    course_title: str
    toss_client_key: str
    customer_key: str


@router.post("/payments/checkout", response_model=CreateOrderOut)
def create_order(
    body: CreateOrderIn,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    """주문 생성 — 서버가 금액을 확정 저장하고 order_uid(토스 orderId)를 발급한다(status=pending).

    이미 수강 중이면 결제할 이유가 없어 409(already_enrolled). 같은 코스에 살아있는 pending 주문이
    있으면 새로 만들지 않고 그대로 재사용한다(연타·새로고침에 주문이 쌓이지 않게 — 금액은 서버
    산출값이라 재사용해도 동일)."""
    c = _load_active_course(db, body.course_id)
    if _is_enrolled(db, principal.id, c.id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"reason": "already_enrolled", "message": "이미 수강 중인 코스예요."},
        )
    amount = demo_course_price(c)
    provider = _pg_provider()
    # 살아있는 pending 주문 재사용 — 금액이 바뀌었을 리 없지만(서버 산출) 방어적으로 맞춰 둔다.
    order = (
        db.query(CourseOrder)
        .filter(
            CourseOrder.student_id == principal.id,
            CourseOrder.course_id == c.id,
            CourseOrder.status == "pending",
        )
        .order_by(CourseOrder.created_at.desc())
        .first()
    )
    if order is None:
        # 토스 orderId 규격(6~64자, 영문/숫자/-_) 안에서 유니크하게. 결정적 UUID로 충돌 사실상 0.
        order_uid = f"catchap_{new_uuid().replace('-', '')}"
        order = CourseOrder(
            student_id=principal.id,
            course_id=c.id,
            order_uid=order_uid,
            amount=amount,
            status="pending",
            provider=provider,
        )
        db.add(order)
        try:
            db.commit()
        except IntegrityError:
            # order_uid 충돌은 사실상 없지만, 있으면 정직하게 재시도를 유도한다(가짜 성공 금지).
            db.rollback()
            raise HTTPException(
                status.HTTP_409_CONFLICT, detail="주문 생성이 겹쳤어요. 다시 시도해 주세요."
            )
    else:
        order.amount = amount
        order.provider = provider
        db.commit()
    s = get_settings()
    return CreateOrderOut(
        order_uid=order.order_uid,
        amount=order.amount,
        provider=order.provider,
        course_title=c.title,
        toss_client_key=s.TOSS_CLIENT_KEY if s.toss_enabled else "",
        customer_key=f"catchap_{principal.id}",
    )


class ConfirmIn(BaseModel):
    order_uid: str
    amount: int
    # 실제 토스 승인은 결제창이 준 paymentKey가 필요하다. mock 결제는 없어도 된다.
    payment_key: str | None = None
    # 결제 수단 표시용(mock에서 프런트 선택값). 실제 토스는 승인 응답의 method로 덮어쓴다.
    method: str | None = None


class ConfirmOut(BaseModel):
    ok: bool
    enrolled: bool
    course_id: str
    order_uid: str
    amount: int
    method: str | None


def _confirm_with_toss(payment_key: str, order_uid: str, amount: int) -> tuple[bool, str | None, str | None]:
    """실제 토스 승인 — (성공여부, 결제수단, 실패사유)를 돌려준다. 키가 있을 때만 이 경로를 탄다.

    표준 토스 승인: 시크릿 키 Basic 인증으로 confirm API를 호출하고, 서버가 확정한 amount로만
    승인한다(클라이언트 금액을 그대로 믿지 않는다 — 이미 confirm 진입 전에 주문 금액과 대조함).
    실패는 성공으로 둔갑시키지 않고 사유를 그대로 전달한다."""
    import base64

    import httpx

    secret = get_settings().TOSS_SECRET_KEY
    # 토스 규격: "{secret_key}:" 를 base64 (비밀번호 없는 Basic 인증)
    token = base64.b64encode(f"{secret}:".encode()).decode()
    try:
        r = httpx.post(
            _TOSS_CONFIRM_URL,
            headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
            json={"paymentKey": payment_key, "orderId": order_uid, "amount": amount},
            timeout=_TOSS_TIMEOUT_SEC,
        )
    except httpx.HTTPError as e:
        _log.warning("토스 승인 요청 실패 order=%s: %s", order_uid, e)
        return False, None, "결제 승인 요청에 실패했어요. 잠시 후 다시 시도해 주세요."
    if r.status_code == 200:
        body = r.json()
        return True, body.get("method"), None
    # 토스 오류 응답 — message를 그대로 노출(정직)
    try:
        msg = r.json().get("message") or "결제 승인이 거절됐어요."
    except Exception:  # noqa: BLE001
        msg = "결제 승인이 거절됐어요."
    return False, None, msg


@router.post("/payments/confirm", response_model=ConfirmOut)
def confirm_payment(
    body: ConfirmIn,
    background_tasks: BackgroundTasks,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    """결제 확정 — 금액 대조(위변조 방어) → 실제/모의 승인 → 수강신청 active.

    멱등: 이미 paid 처리된 주문을 다시 confirm 하면(네트워크 재시도 등) 재승인 없이 성공을 돌려준다.
    실패는 주문을 failed로 남기고 사유를 그대로 전한다(가짜 성공 금지)."""
    order = (
        db.query(CourseOrder)
        .filter(
            CourseOrder.order_uid == body.order_uid,
            CourseOrder.student_id == principal.id,  # 소유권 — 남의 주문 확정 불가
        )
        .first()
    )
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="주문을 찾을 수 없어요.")

    # 이미 결제 완료 — 멱등 성공(재확정/새로고침). 수강신청은 이미 활성.
    if order.status == "paid":
        return ConfirmOut(
            ok=True, enrolled=True, course_id=order.course_id,
            order_uid=order.order_uid, amount=order.amount, method=order.method,
        )
    if order.status != "pending":
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="이미 종료된 주문이에요. 다시 시도해 주세요."
        )

    # 금액 위변조 방어 — 클라이언트가 보낸 금액이 서버가 확정한 주문 금액과 다르면 승인하지 않는다.
    if body.amount != order.amount:
        order.status = "failed"
        order.fail_reason = "금액 불일치"
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="결제 금액이 올바르지 않아요.")

    # 코스가 그새 비활성화됐으면 승인하지 않는다.
    c = db.get(Course, order.course_id)
    if c is None or c.status != "active":
        order.status = "failed"
        order.fail_reason = "코스 비활성"
        db.commit()
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="코스를 찾을 수 없어요.")

    method = body.method
    if order.provider == "toss":
        if not body.payment_key:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="결제 정보가 없어요.")
        ok, toss_method, reason = _confirm_with_toss(body.payment_key, order.order_uid, order.amount)
        if not ok:
            order.status = "failed"
            order.fail_reason = (reason or "")[:200]
            db.commit()
            raise HTTPException(
                status.HTTP_402_PAYMENT_REQUIRED,
                detail=reason or "결제 승인이 거절됐어요.",
            )
        order.payment_key = body.payment_key
        method = toss_method or method
    else:
        # mock 승인 — 실제 돈 이동 없이 결제 UX만 재현(로컬·데모). 사실을 숨기지 않는다(provider=mock).
        order.payment_key = f"mock_{order.order_uid}"
        method = method or "간편결제"

    order.status = "paid"
    order.method = method
    order.paid_at = datetime.now()
    db.commit()

    # 결제 완료 → 수강신청 활성화(무료 신청과 같은 단일 진실원). 새로 active면 완료 알림.
    newly_active = activate_enrollment(db, principal.id, order.course_id)
    if newly_active:
        background_tasks.add_task(_notify_enroll, principal.id, order.course_id)

    return ConfirmOut(
        ok=True, enrolled=True, course_id=order.course_id,
        order_uid=order.order_uid, amount=order.amount, method=order.method,
    )
