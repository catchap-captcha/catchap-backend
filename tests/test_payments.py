"""카카오페이·토스페이먼츠 코스 결제 API."""

from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints import payments
from app.core.config import get_settings
from app.core.permissions import Principal, require_content_author, require_student
from app.core.security import hash_password, new_uuid
from app.db.session import get_db
from app.main import app
from app.models import Course, CourseEnrollment, CourseOrder, StudentProfile
from app.services.payment_gateways import ApprovedPayment, KakaoReady


@pytest.fixture()
def payment_context(db, monkeypatch):
    """PG HTTP는 가짜 응답으로 교체하고 API/DB 상태 전이만 검증한다."""
    for key, value in {
        "ENV": "dev",
        "PAYMENT_MOCK_ENABLED": "true",
        "TOSS_CLIENT_KEY": "test_ck_example",
        "TOSS_SECRET_KEY": "test_sk_example",
        "KAKAOPAY_CID": "TC0ONETIME",
        "KAKAOPAY_SECRET_KEY": "kakao_test_secret",
        "KAKAOPAY_CID_SECRET": "",
        "BACKEND_URL": "http://api.test",
        "FRONTEND_URL": "http://frontend.test",
        "PAYMENT_SUCCESS_URL": "http://frontend.test/payment/success",
        "PAYMENT_FAIL_URL": "http://frontend.test/payment/fail",
        "PAYMENT_CANCEL_URL": "http://frontend.test/payment/cancel",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()

    student = StudentProfile(
        organization_id=None,
        class_id=None,
        student_login_id="pay-student@example.test",
        student_code="CAT-PAY-01",
        password_hash=hash_password("Password123!"),
        nickname="결제학생",
        grade_band="adult",
    )
    db.add(student)
    db.flush()
    course = Course(
        instructor_id=new_uuid(),
        subject="일반",
        title="결제 테스트 코스",
        price=49_000,
        status="active",
    )
    db.add(course)
    db.commit()
    principal = Principal(
        kind="student", id=student.id, role="student", student=student
    )

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_student] = lambda: principal
    # 응답 후 SMTP 알림이 테스트 DB 밖으로 나가지 않게 한다.
    monkeypatch.setattr(payments, "_notify_enroll", lambda *_args, **_kwargs: None)
    with TestClient(app) as client:
        yield {
            "client": client,
            "db": db,
            "student": student,
            "course": course,
            "principal": principal,
        }
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def _create_order(ctx, provider: str) -> dict:
    response = ctx["client"].post(
        "/api/v1/payments/checkout",
        json={"course_id": ctx["course"].id, "provider": provider},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_checkout_and_order_use_server_price(payment_context):
    ctx = payment_context
    checkout = ctx["client"].get(f"/api/v1/courses/{ctx['course'].id}/checkout")
    assert checkout.status_code == 200
    assert checkout.json()["amount"] == 49_000
    assert checkout.json()["available_providers"] == ["toss", "kakaopay", "mock"]

    order_data = _create_order(ctx, "toss")
    assert order_data["amount"] == 49_000
    assert order_data["provider"] == "toss"
    assert order_data["toss_client_key"] == "test_ck_example"
    order = (
        ctx["db"].query(CourseOrder)
        .filter(CourseOrder.order_uid == order_data["order_uid"])
        .one()
    )
    assert order.amount == 49_000
    assert order.status == "pending"


def test_paid_course_cannot_bypass_payment_with_free_enroll_api(payment_context):
    ctx = payment_context
    response = ctx["client"].post(f"/api/v1/courses/{ctx['course'].id}/enroll")
    assert response.status_code == 402
    assert response.json()["detail"]["reason"] == "payment_required"


def test_pending_order_keeps_price_snapshot_and_switching_provider_cancels_it(
    payment_context,
):
    ctx = payment_context
    toss_order = _create_order(ctx, "toss")
    ctx["course"].price = 66_000
    ctx["db"].commit()
    reused = _create_order(ctx, "toss")
    assert reused["order_uid"] == toss_order["order_uid"]
    assert reused["amount"] == 49_000

    kakao_order = _create_order(ctx, "kakaopay")
    assert kakao_order["order_uid"] != toss_order["order_uid"]
    ctx["db"].expire_all()
    old = (
        ctx["db"].query(CourseOrder)
        .filter(CourseOrder.order_uid == toss_order["order_uid"])
        .one()
    )
    assert old.status == "cancelled"
    assert old.cancel_reason == "결제수단 변경"
    assert (
        ctx["db"].query(CourseOrder)
        .filter(
            CourseOrder.student_id == ctx["student"].id,
            CourseOrder.course_id == ctx["course"].id,
            CourseOrder.status == "pending",
        )
        .count()
        == 1
    )


def test_tampered_amount_is_rejected(payment_context):
    ctx = payment_context
    order = _create_order(ctx, "toss")
    response = ctx["client"].post(
        "/api/v1/payments/confirm",
        json={
            "order_uid": order["order_uid"],
            "amount": 100,
            "payment_key": "payment-key",
        },
    )
    assert response.status_code == 400
    ctx["db"].expire_all()
    saved = (
        ctx["db"].query(CourseOrder)
        .filter(CourseOrder.order_uid == order["order_uid"])
        .one()
    )
    assert saved.status == "failed"
    assert saved.fail_reason == "금액 불일치"


def test_toss_confirm_activates_enrollment(payment_context, monkeypatch):
    ctx = payment_context
    order = _create_order(ctx, "toss")

    def fake_confirm(_self, payment_key, order_id, amount):
        assert payment_key == "toss-payment-key"
        return ApprovedPayment(
            provider_payment_id=payment_key,
            order_id=order_id,
            amount=amount,
            status="DONE",
            method="카드",
            receipt_url="https://receipt.test/toss",
        )

    monkeypatch.setattr(payments.TossPaymentsGateway, "confirm", fake_confirm)
    response = ctx["client"].post(
        "/api/v1/payments/confirm",
        json={
            "order_uid": order["order_uid"],
            "amount": order["amount"],
            "payment_key": "toss-payment-key",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["receipt_url"] == "https://receipt.test/toss"
    ctx["db"].expire_all()
    saved = (
        ctx["db"].query(CourseOrder)
        .filter(CourseOrder.order_uid == order["order_uid"])
        .one()
    )
    assert saved.status == "paid"
    assert saved.payment_key == "toss-payment-key"
    enrollment = (
        ctx["db"].query(CourseEnrollment)
        .filter(
            CourseEnrollment.student_id == ctx["student"].id,
            CourseEnrollment.course_id == ctx["course"].id,
        )
        .one()
    )
    assert enrollment.status == "active"


def test_kakaopay_qr_ready_and_approve_callback(payment_context, monkeypatch):
    ctx = payment_context
    order = _create_order(ctx, "kakaopay")
    captured = {}

    def fake_ready(_self, **kwargs):
        captured.update(kwargs)
        return KakaoReady(
            tid="T1234567890",
            next_redirect_pc_url="https://kakao.test/qr",
            next_redirect_mobile_url="https://kakao.test/mobile",
            next_redirect_app_url="kakaotalk://pay",
        )

    monkeypatch.setattr(payments.KakaoPayGateway, "ready", fake_ready)
    ready_response = ctx["client"].post(
        "/api/v1/payments/kakaopay/ready",
        json={"order_uid": order["order_uid"]},
    )
    assert ready_response.status_code == 200, ready_response.text
    assert ready_response.json()["next_redirect_pc_url"] == "https://kakao.test/qr"
    approval_query = parse_qs(urlsplit(captured["approval_url"]).query)
    state = approval_query["state"][0]

    def fake_approve(_self, *, tid, order_id, user_id, pg_token, amount):
        assert tid == "T1234567890"
        assert pg_token == "pg-token"
        assert user_id == ctx["student"].id
        return ApprovedPayment(
            provider_payment_id=tid,
            order_id=order_id,
            amount=amount,
            status="DONE",
            method="MONEY",
        )

    monkeypatch.setattr(payments.KakaoPayGateway, "approve", fake_approve)
    approve_response = ctx["client"].get(
        "/api/v1/payments/kakaopay/approve",
        params={
            "order_uid": order["order_uid"],
            "state": state,
            "pg_token": "pg-token",
        },
        follow_redirects=False,
    )
    assert approve_response.status_code == 303
    assert approve_response.headers["location"].startswith(
        "http://frontend.test/payment/success"
    )
    ctx["db"].expire_all()
    saved = (
        ctx["db"].query(CourseOrder)
        .filter(CourseOrder.order_uid == order["order_uid"])
        .one()
    )
    assert saved.status == "paid"
    assert saved.payment_key == "T1234567890"
    assert saved.callback_token_hash is None
    assert (
        ctx["db"].query(CourseEnrollment)
        .filter(
            CourseEnrollment.student_id == ctx["student"].id,
            CourseEnrollment.course_id == ctx["course"].id,
            CourseEnrollment.status == "active",
        )
        .count()
        == 1
    )


def test_kakao_callback_rejects_wrong_state(payment_context, monkeypatch):
    ctx = payment_context
    order = _create_order(ctx, "kakaopay")
    monkeypatch.setattr(
        payments.KakaoPayGateway,
        "ready",
        lambda _self, **_kwargs: KakaoReady(
            tid="T-wrong-state",
            next_redirect_pc_url="https://kakao.test/qr",
            next_redirect_mobile_url="",
            next_redirect_app_url="",
        ),
    )
    assert (
        ctx["client"]
        .post(
            "/api/v1/payments/kakaopay/ready",
            json={"order_uid": order["order_uid"]},
        )
        .status_code
        == 200
    )
    response = ctx["client"].get(
        "/api/v1/payments/kakaopay/approve",
        params={
            "order_uid": order["order_uid"],
            "state": "forged",
            "pg_token": "pg-token",
        },
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_full_cancel_refunds_and_revokes_enrollment(payment_context, monkeypatch):
    ctx = payment_context
    order = _create_order(ctx, "toss")
    monkeypatch.setattr(
        payments.TossPaymentsGateway,
        "confirm",
        lambda _self, payment_key, order_id, amount: ApprovedPayment(
            provider_payment_id=payment_key,
            order_id=order_id,
            amount=amount,
            status="DONE",
            method="카드",
        ),
    )
    assert (
        ctx["client"]
        .post(
            "/api/v1/payments/confirm",
            json={
                "order_uid": order["order_uid"],
                "amount": order["amount"],
                "payment_key": "toss-cancel-key",
            },
        )
        .status_code
        == 200
    )

    def fake_cancel(_self, payment_key, *, reason, idempotency_key):
        assert payment_key == "toss-cancel-key"
        assert idempotency_key == f"cancel-{order['order_uid']}"
        return ApprovedPayment(
            provider_payment_id=payment_key,
            order_id=order["order_uid"],
            amount=order["amount"],
            status="CANCELED",
        )

    monkeypatch.setattr(payments.TossPaymentsGateway, "cancel", fake_cancel)
    response = ctx["client"].post(
        f"/api/v1/payments/{order['order_uid']}/cancel",
        json={"reason": "구매 취소"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "refunded"
    ctx["db"].expire_all()
    enrollment = (
        ctx["db"].query(CourseEnrollment)
        .filter(
            CourseEnrollment.student_id == ctx["student"].id,
            CourseEnrollment.course_id == ctx["course"].id,
        )
        .one()
    )
    assert enrollment.status == "withdrawn"


def test_toss_webhook_rechecks_provider_before_activating(payment_context, monkeypatch):
    ctx = payment_context
    order = _create_order(ctx, "toss")

    def fake_fetch(_self, payment_key):
        assert payment_key == "webhook-payment-key"
        return ApprovedPayment(
            provider_payment_id=payment_key,
            order_id=order["order_uid"],
            amount=order["amount"],
            status="DONE",
            method="간편결제",
        )

    monkeypatch.setattr(payments.TossPaymentsGateway, "fetch", fake_fetch)
    response = ctx["client"].post(
        "/api/v1/payments/webhooks/toss",
        json={
            "eventType": "PAYMENT_STATUS_CHANGED",
            # 본문에는 조작된 금액을 넣어도 서버는 fake_fetch(실제로는 토스 조회 API)의
            # 검증 결과만 사용한다.
            "data": {"paymentKey": "webhook-payment-key", "totalAmount": 1},
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "paid"
    ctx["db"].expire_all()
    saved = (
        ctx["db"].query(CourseOrder)
        .filter(CourseOrder.order_uid == order["order_uid"])
        .one()
    )
    assert saved.payment_key == "webhook-payment-key"
    assert saved.status == "paid"


def test_course_pricing_endpoint_sets_server_price(payment_context):
    ctx = payment_context
    instructor = Principal(
        kind="user",
        id=ctx["course"].instructor_id,
        role="instructor",
    )
    app.dependency_overrides[require_content_author] = lambda: instructor
    response = ctx["client"].put(
        f"/api/v1/ops/courses/{ctx['course'].id}/pricing",
        json={"price": 60_000, "sale_price": 39_000, "sale_ends_at": None},
    )
    assert response.status_code == 200, response.text
    assert response.json()["effective_price"] == 39_000
    ctx["db"].expire_all()
    assert ctx["db"].get(Course, ctx["course"].id).price == 60_000
