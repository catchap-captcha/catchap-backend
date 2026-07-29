"""외부 PG HTTP 규격 단위 테스트 — 네트워크 없이 요청 헤더·본문과 응답 검증."""

import base64
import json

import httpx
import pytest

from app.services.payment_gateways import (
    KakaoPayGateway,
    PaymentGatewayError,
    PortOneGateway,
    TossPaymentsGateway,
)


def test_toss_confirm_uses_basic_auth_and_verifies_response():
    def handler(request: httpx.Request) -> httpx.Response:
        expected = base64.b64encode(b"test_sk:").decode()
        assert request.headers["Authorization"] == f"Basic {expected}"
        assert request.headers["Idempotency-Key"] == "confirm-catchap_order_1"
        assert json.loads(request.content) == {
            "paymentKey": "pay-key",
            "orderId": "catchap_order_1",
            "amount": 49_000,
        }
        return httpx.Response(
            200,
            json={
                "paymentKey": "pay-key",
                "orderId": "catchap_order_1",
                "totalAmount": 49_000,
                "status": "DONE",
                "method": "카드",
                "receipt": {"url": "https://receipt.test/1"},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        payment = TossPaymentsGateway("test_sk", client=client).confirm(
            "pay-key", "catchap_order_1", 49_000
        )
    assert payment.status == "DONE"
    assert payment.receipt_url == "https://receipt.test/1"


def test_toss_confirm_rejects_amount_mismatch():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "paymentKey": "pay-key",
                "orderId": "catchap_order_1",
                "totalAmount": 10,
                "status": "DONE",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PaymentGatewayError, match="일치하지"):
            TossPaymentsGateway("test_sk", client=client).confirm(
                "pay-key", "catchap_order_1", 49_000
            )


def test_kakaopay_ready_uses_secret_header_and_server_amount():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "SECRET_KEY kakao-secret"
        body = json.loads(request.content)
        assert body["cid"] == "TC0ONETIME"
        assert body["partner_order_id"] == "catchap_order_1"
        assert body["partner_user_id"] == "student-uuid"
        assert body["total_amount"] == 49_000
        assert body["tax_free_amount"] == 0
        return httpx.Response(
            200,
            json={
                "tid": "T123",
                "next_redirect_pc_url": "https://kakao.test/qr",
                "next_redirect_mobile_url": "https://kakao.test/mobile",
                "next_redirect_app_url": "kakaotalk://pay",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ready = KakaoPayGateway(
            "TC0ONETIME", "kakao-secret", client=client
        ).ready(
            order_id="catchap_order_1",
            user_id="student-uuid",
            item_name="영어 코스",
            amount=49_000,
            approval_url="https://api.test/approve",
            cancel_url="https://api.test/cancel",
            fail_url="https://api.test/fail",
        )
    assert ready.tid == "T123"
    assert ready.next_redirect_pc_url == "https://kakao.test/qr"


def test_kakaopay_approve_verifies_tid_order_and_amount():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["pg_token"] == "pg-token"
        return httpx.Response(
            200,
            json={
                "tid": "T123",
                "partner_order_id": "catchap_order_1",
                "payment_method_type": "MONEY",
                "amount": {"total": 49_000},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        payment = KakaoPayGateway(
            "TC0ONETIME", "kakao-secret", client=client
        ).approve(
            tid="T123",
            order_id="catchap_order_1",
            user_id="student-uuid",
            pg_token="pg-token",
            amount=49_000,
        )
    assert payment.provider_payment_id == "T123"
    assert payment.status == "DONE"
    assert payment.method == "MONEY"


# ===================== 포트원(PortOne) V2 =====================


def _portone_payment(status: str = "PAID", total: int = 49_000) -> dict:
    return {
        "id": "catchap_order_1",
        "status": status,
        "amount": {"total": total, "taxFree": 0, "vat": 4_454},
        "channel": {"pgProvider": "TOSSPAYMENTS"},
        "method": {"type": "PaymentMethodCard"},
        "receiptUrl": "https://receipt.example/1",
    }


def test_portone_verify_uses_portone_auth_and_reads_amount_total():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "PortOne secret-1"
        assert request.url.path == "/payments/catchap_order_1"
        assert request.method == "GET"
        return httpx.Response(200, json=_portone_payment())

    gw = PortOneGateway("secret-1", client=httpx.Client(transport=httpx.MockTransport(handler)))
    payment = gw.verify("catchap_order_1")
    assert payment.provider_payment_id == "catchap_order_1"
    # 포트원은 고객사가 채번한 paymentId 가 곧 주문번호다
    assert payment.order_id == "catchap_order_1"
    assert payment.amount == 49_000
    assert payment.status == "PAID"
    assert payment.method == "TOSSPAYMENTS"
    assert payment.receipt_url == "https://receipt.example/1"


def test_portone_verify_rejects_broken_amount():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "catchap_order_1", "status": "PAID"})

    gw = PortOneGateway("secret-1", client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(PaymentGatewayError):
        gw.verify("catchap_order_1")


def test_portone_verify_marks_5xx_uncertain():
    """PG 5xx는 결제 여부를 확정할 수 없다 — 주문을 실패로 끊으면 안 된다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "server error"})

    gw = PortOneGateway("secret-1", client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(PaymentGatewayError) as err:
        gw.verify("catchap_order_1")
    assert err.value.uncertain is True


def test_portone_cancel_posts_reason_and_rechecks_status():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen.append("cancel")
            assert request.url.path == "/payments/catchap_order_1/cancel"
            body = json.loads(request.content)
            assert body["reason"] == "학습자 요청"
            assert body["storeId"] == "store-1"
            assert body["amount"] == 49_000
            return httpx.Response(200, json={"cancellation": {"status": "SUCCEEDED"}})
        seen.append("verify")
        return httpx.Response(200, json=_portone_payment(status="CANCELLED"))

    gw = PortOneGateway(
        "secret-1", store_id="store-1", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    out = gw.cancel("catchap_order_1", amount=49_000, reason="학습자 요청")
    # 취소 응답만 믿지 않고 재조회로 확인한다
    assert seen == ["cancel", "verify"]
    assert out.status == "CANCELLED"


def test_portone_cancel_fails_when_status_not_cancelled():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={})
        return httpx.Response(200, json=_portone_payment(status="PAID"))  # 반영 안 됨

    gw = PortOneGateway("secret-1", client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(PaymentGatewayError) as err:
        gw.cancel("catchap_order_1", amount=49_000, reason="학습자 요청")
    assert err.value.uncertain is True
