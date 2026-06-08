"""HyperswitchAdapter — PaymentSwitchPort implementation via Hyperswitch API.

WHY: ADR-015. Hyperswitch (Apache 2.0, Juspay) is the payment orchestration layer.
All calls go through PaymentSwitchPort — this adapter is replaceable (I-20).
Hyperswitch runs locally at :8096 (see SERVICE-MAP.md, Sprint 9).

I-10: If HYPERSWITCH_BASE_URL is not configured, raise NotImplementedError
      (never generate fake payment data).
"""

from __future__ import annotations

import os

import httpx

from src.ports.payment_switch_port import (
    PaymentRequest,
    PaymentResult,
    PaymentStatus,
    PaymentSwitchPort,
)

_STATUS_MAP: dict[str, PaymentStatus] = {
    "succeeded": PaymentStatus.CAPTURED,
    "requires_capture": PaymentStatus.AUTHORIZED,
    "requires_action": PaymentStatus.PENDING_3DS,
    "processing": PaymentStatus.PENDING_3DS,
    "failed": PaymentStatus.FAILED,
    "cancelled": PaymentStatus.VOIDED,
    "refunded": PaymentStatus.REFUNDED,
    "partially_refunded": PaymentStatus.REFUNDED,
}


class HyperswitchAdapter(PaymentSwitchPort):
    """Concrete PaymentSwitchPort backed by Hyperswitch REST API.

    Config (from environment / .env):
        HYPERSWITCH_BASE_URL  — e.g. http://localhost:8096
        HYPERSWITCH_API_KEY   — Hyperswitch API key
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._base_url = (base_url or os.environ.get("HYPERSWITCH_BASE_URL", "")).rstrip("/")
        self._api_key = api_key or os.environ.get("HYPERSWITCH_API_KEY", "")

        if not self._base_url:  # I-10: no fake integrations
            raise NotImplementedError(
                "HYPERSWITCH_BASE_URL not configured. "
                "Deploy Hyperswitch (Sprint 9) and set env var."
            )

    @property
    def _headers(self) -> dict[str, str]:
        return {"api-key": self._api_key, "Content-Type": "application/json"}

    async def authorize(self, request: PaymentRequest) -> PaymentResult:
        payload = {
            "amount": request.amount_minor,
            "currency": request.currency,
            "payment_method": "card",
            "payment_method_data": {"card": {"card_token": request.card_token}},
            "customer_id": request.customer_id,
            "merchant_id": request.merchant_id,
            "confirm": True,
            "capture_method": "manual",
            "metadata": request.metadata or {},
        }
        async with httpx.AsyncClient(headers=self._headers) as client:
            resp = await client.post(f"{self._base_url}/payments", json=payload)
            resp.raise_for_status()
            data = resp.json()
        return self._parse_result(data)

    async def capture(self, transaction_id: str, amount_minor: int | None = None) -> PaymentResult:
        payload = {}
        if amount_minor is not None:
            payload["amount_to_capture"] = amount_minor
        async with httpx.AsyncClient(headers=self._headers) as client:
            resp = await client.post(
                f"{self._base_url}/payments/{transaction_id}/capture", json=payload
            )
            resp.raise_for_status()
            data = resp.json()
        return self._parse_result(data)

    async def refund(self, transaction_id: str, amount_minor: int, reason: str) -> PaymentResult:
        payload = {"payment_id": transaction_id, "amount": amount_minor, "reason": reason}
        async with httpx.AsyncClient(headers=self._headers) as client:
            resp = await client.post(f"{self._base_url}/refunds", json=payload)
            resp.raise_for_status()
            data = resp.json()
        return PaymentResult(
            status=PaymentStatus.REFUNDED,
            transaction_id=data.get("refund_id", transaction_id),
            processor_response_code=data.get("connector_refund_id", ""),
            amount_minor=data.get("amount", amount_minor),
            currency=data.get("currency", ""),
        )

    async def void(self, transaction_id: str) -> PaymentResult:
        async with httpx.AsyncClient(headers=self._headers) as client:
            resp = await client.post(
                f"{self._base_url}/payments/{transaction_id}/cancel",
                json={"cancellation_reason": "requested_by_customer"},
            )
            resp.raise_for_status()
            data = resp.json()
        return self._parse_result(data)

    async def get_payment_status(self, transaction_id: str) -> PaymentResult:
        async with httpx.AsyncClient(headers=self._headers) as client:
            resp = await client.get(f"{self._base_url}/payments/{transaction_id}")
            resp.raise_for_status()
            data = resp.json()
        return self._parse_result(data)

    @staticmethod
    def _parse_result(data: dict) -> PaymentResult:
        raw_status = data.get("status", "failed")
        status = _STATUS_MAP.get(raw_status, PaymentStatus.FAILED)
        return PaymentResult(
            status=status,
            transaction_id=data.get("payment_id", ""),
            processor_response_code=data.get("connector_transaction_id", raw_status),
            amount_minor=data.get("amount", 0),
            currency=data.get("currency", ""),
            error_message=data.get("error_message"),
        )
