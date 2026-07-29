"""외부 결제사 HTTP 어댑터.

엔드포인트는 주문 소유권·상태 전이를 담당하고, 이 모듈은 PG별 HTTP 규격만 담당한다.
카드번호·생년월일 같은 결제수단 원문은 받거나 저장하지 않는다. 모든 성공 응답은 주문번호,
금액, 결제 상태를 다시 검증해 HTTP 200만으로 결제 성공을 판단하지 않는다.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx


class PaymentGatewayError(RuntimeError):
    """사용자에게 노출해도 되는 PG 실패.

    provider_code는 운영 로그·CS용이며 비밀정보를 포함하지 않는다.
    """

    def __init__(
        self,
        message: str,
        *,
        provider_code: str | None = None,
        uncertain: bool = False,
    ):
        super().__init__(message)
        self.message = message
        self.provider_code = provider_code
        # 네트워크 단절/PG 5xx는 결제가 처리됐는지 확정할 수 없다. 이 경우 로컬 주문을
        # failed로 종결하지 않고 pending으로 남겨 웹훅·재조회로 복구해야 한다.
        self.uncertain = uncertain


@dataclass(frozen=True)
class ApprovedPayment:
    provider_payment_id: str
    order_id: str
    amount: int
    status: str
    method: str | None = None
    receipt_url: str | None = None


@dataclass(frozen=True)
class KakaoReady:
    tid: str
    next_redirect_pc_url: str
    next_redirect_mobile_url: str
    next_redirect_app_url: str


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise PaymentGatewayError("결제사 응답을 확인할 수 없어요.") from exc
    if not isinstance(body, dict):
        raise PaymentGatewayError("결제사 응답 형식이 올바르지 않아요.")
    return body


def _error_message(body: dict[str, Any], fallback: str) -> tuple[str, str | None]:
    message = body.get("message") or body.get("error_message") or fallback
    code = body.get("code") or body.get("error_code")
    return str(message)[:200], str(code)[:80] if code is not None else None


class TossPaymentsGateway:
    API_BASE = "https://api.tosspayments.com/v1"

    def __init__(
        self,
        secret_key: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
    ):
        if not secret_key.strip():
            raise ValueError("토스페이먼츠 시크릿 키가 필요합니다.")
        token = base64.b64encode(f"{secret_key}:".encode()).decode()
        self._headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
        }
        self._client = client
        self._timeout = timeout

    def _request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = dict(self._headers)
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key[:300]
        try:
            if self._client is not None:
                response = self._client.request(method, url, headers=headers, json=json)
            else:
                response = httpx.request(
                    method, url, headers=headers, json=json, timeout=self._timeout
                )
        except httpx.HTTPError as exc:
            raise PaymentGatewayError(
                "토스페이먼츠 승인 결과를 확인하지 못했어요. 잠시 후 상태를 확인해 주세요.",
                uncertain=True,
            ) from exc
        body = _safe_json(response)
        if response.status_code < 200 or response.status_code >= 300:
            message, code = _error_message(body, "토스페이먼츠 요청이 거절됐어요.")
            raise PaymentGatewayError(
                message, provider_code=code, uncertain=response.status_code >= 500
            )
        return body

    @staticmethod
    def _payment(body: dict[str, Any]) -> ApprovedPayment:
        payment_key = str(body.get("paymentKey") or "")
        order_id = str(body.get("orderId") or "")
        try:
            amount = int(body.get("totalAmount"))
        except (TypeError, ValueError) as exc:
            raise PaymentGatewayError("토스페이먼츠 결제 금액을 확인할 수 없어요.") from exc
        if not payment_key or not order_id:
            raise PaymentGatewayError("토스페이먼츠 결제 식별값이 없어요.")
        receipt = body.get("receipt") if isinstance(body.get("receipt"), dict) else {}
        return ApprovedPayment(
            provider_payment_id=payment_key,
            order_id=order_id,
            amount=amount,
            status=str(body.get("status") or ""),
            method=str(body.get("method")) if body.get("method") else None,
            receipt_url=str(receipt.get("url")) if receipt.get("url") else None,
        )

    def confirm(self, payment_key: str, order_id: str, amount: int) -> ApprovedPayment:
        body = self._request(
            "POST",
            f"{self.API_BASE}/payments/confirm",
            json={"paymentKey": payment_key, "orderId": order_id, "amount": amount},
            idempotency_key=f"confirm-{order_id}",
        )
        payment = self._payment(body)
        if (
            payment.provider_payment_id != payment_key
            or payment.order_id != order_id
            or payment.amount != amount
            or payment.status != "DONE"
        ):
            raise PaymentGatewayError("토스페이먼츠 승인 결과가 주문 정보와 일치하지 않아요.")
        return payment

    def fetch(self, payment_key: str) -> ApprovedPayment:
        body = self._request(
            "GET", f"{self.API_BASE}/payments/{quote(payment_key, safe='')}"
        )
        payment = self._payment(body)
        if payment.provider_payment_id != payment_key:
            raise PaymentGatewayError("토스페이먼츠 조회 결과가 결제 정보와 일치하지 않아요.")
        return payment

    def cancel(
        self, payment_key: str, *, reason: str, idempotency_key: str
    ) -> ApprovedPayment:
        body = self._request(
            "POST",
            f"{self.API_BASE}/payments/{quote(payment_key, safe='')}/cancel",
            json={"cancelReason": reason[:200]},
            idempotency_key=idempotency_key,
        )
        payment = self._payment(body)
        if payment.provider_payment_id != payment_key:
            raise PaymentGatewayError("토스페이먼츠 취소 결과가 결제 정보와 일치하지 않아요.")
        if payment.status not in ("CANCELED", "PARTIAL_CANCELED"):
            raise PaymentGatewayError("토스페이먼츠에서 결제가 취소되지 않았어요.")
        return payment


class KakaoPayGateway:
    API_BASE = "https://open-api.kakaopay.com/online/v1/payment"

    def __init__(
        self,
        cid: str,
        secret_key: str,
        *,
        cid_secret: str = "",
        client: httpx.Client | None = None,
        timeout: float = 11.0,
    ):
        if not cid.strip() or not secret_key.strip():
            raise ValueError("카카오페이 CID와 Secret key가 필요합니다.")
        self.cid = cid.strip()
        self.cid_secret = cid_secret.strip()
        self._headers = {
            "Authorization": f"SECRET_KEY {secret_key.strip()}",
            "Content-Type": "application/json",
        }
        self._client = client
        self._timeout = timeout

    def _request(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        payload = {"cid": self.cid, **payload}
        if self.cid_secret:
            payload["cid_secret"] = self.cid_secret
        try:
            if self._client is not None:
                response = self._client.post(
                    f"{self.API_BASE}/{path}", headers=self._headers, json=payload
                )
            else:
                response = httpx.post(
                    f"{self.API_BASE}/{path}",
                    headers=self._headers,
                    json=payload,
                    timeout=self._timeout,
                )
        except httpx.HTTPError as exc:
            raise PaymentGatewayError(
                "카카오페이 처리 결과를 확인하지 못했어요. 잠시 후 상태를 확인해 주세요.",
                uncertain=True,
            ) from exc
        body = _safe_json(response)
        if response.status_code < 200 or response.status_code >= 300:
            message, code = _error_message(body, "카카오페이 요청이 거절됐어요.")
            raise PaymentGatewayError(
                message, provider_code=code, uncertain=response.status_code >= 500
            )
        return body

    def ready(
        self,
        *,
        order_id: str,
        user_id: str,
        item_name: str,
        amount: int,
        approval_url: str,
        cancel_url: str,
        fail_url: str,
    ) -> KakaoReady:
        body = self._request(
            "ready",
            {
                "partner_order_id": order_id,
                "partner_user_id": user_id,
                "item_name": item_name[:100],
                "quantity": 1,
                "total_amount": amount,
                "tax_free_amount": 0,
                "approval_url": approval_url,
                "cancel_url": cancel_url,
                "fail_url": fail_url,
            },
        )
        values = {
            "tid": str(body.get("tid") or ""),
            "next_redirect_pc_url": str(body.get("next_redirect_pc_url") or ""),
            "next_redirect_mobile_url": str(body.get("next_redirect_mobile_url") or ""),
            "next_redirect_app_url": str(body.get("next_redirect_app_url") or ""),
        }
        if not values["tid"] or not values["next_redirect_pc_url"]:
            raise PaymentGatewayError("카카오페이 결제 화면을 준비하지 못했어요.")
        return KakaoReady(**values)

    def approve(
        self,
        *,
        tid: str,
        order_id: str,
        user_id: str,
        pg_token: str,
        amount: int,
    ) -> ApprovedPayment:
        body = self._request(
            "approve",
            {
                "tid": tid,
                "partner_order_id": order_id,
                "partner_user_id": user_id,
                "pg_token": pg_token,
            },
        )
        response_order = str(body.get("partner_order_id") or "")
        response_tid = str(body.get("tid") or "")
        amount_body = body.get("amount") if isinstance(body.get("amount"), dict) else {}
        try:
            response_amount = int(amount_body.get("total"))
        except (TypeError, ValueError) as exc:
            raise PaymentGatewayError("카카오페이 결제 금액을 확인할 수 없어요.") from exc
        if response_order != order_id or response_tid != tid or response_amount != amount:
            raise PaymentGatewayError("카카오페이 승인 결과가 주문 정보와 일치하지 않아요.")
        return ApprovedPayment(
            provider_payment_id=response_tid,
            order_id=response_order,
            amount=response_amount,
            status="DONE",
            method=str(body.get("payment_method_type") or "카카오페이"),
        )

    def cancel(self, tid: str, *, amount: int) -> ApprovedPayment:
        body = self._request(
            "cancel",
            {
                "tid": tid,
                "cancel_amount": amount,
                "cancel_tax_free_amount": 0,
            },
        )
        response_tid = str(body.get("tid") or "")
        approved_cancel_amount = body.get("approved_cancel_amount")
        approved_amount = (
            approved_cancel_amount if isinstance(approved_cancel_amount, dict) else {}
        )
        try:
            cancelled = int(approved_amount.get("total", amount))
        except (TypeError, ValueError) as exc:
            raise PaymentGatewayError("카카오페이 취소 금액을 확인할 수 없어요.") from exc
        if response_tid != tid or cancelled != amount:
            raise PaymentGatewayError("카카오페이 취소 결과가 주문 정보와 일치하지 않아요.")
        return ApprovedPayment(
            provider_payment_id=tid,
            order_id="",
            amount=amount,
            status="CANCELED",
            method="카카오페이",
        )
