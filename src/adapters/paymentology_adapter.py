"""PaymentologyAdapter — IssuerPort implementation via Paymentology Companion API.

WHY: ADR-015. Paymentology is the Mastercard-certified issuer processor.
Companion API pattern: BANXE (via Midaz) controls authorisation decisions.
Paymentology sends authorisation requests to BANXE → BANXE approves/declines.

I-10: If PAYMENTOLOGY_BASE_URL not configured, raise NotImplementedError.
I-20: Replaceable with CLOWD9 or other issuer — all calls via IssuerPort.
"""

from __future__ import annotations

import os

import httpx

from src.ports.issuer_port import (
    AuthorisationRequest,
    AuthorisationResponse,
    CardIssueRequest,
    CardIssueResult,
    CardStatus,
    IssuerPort,
)

_CARD_STATUS_MAP: dict[str, CardStatus] = {
    "ACTIVE": CardStatus.ACTIVE,
    "SUSPENDED": CardStatus.SUSPENDED,
    "CANCELLED": CardStatus.CANCELLED,
    "PENDING": CardStatus.PENDING,
}

# ISO 8583 response codes
_RESP_APPROVE = "00"
_RESP_DECLINE = "05"
_RESP_REFER = "01"


class PaymentologyAdapter(IssuerPort):
    """Concrete IssuerPort backed by Paymentology REST/XML API.

    Config (from environment / .env):
        PAYMENTOLOGY_BASE_URL   — e.g. https://api.paymentology.com
        PAYMENTOLOGY_API_KEY    — Paymentology API key
        PAYMENTOLOGY_PROGRAM_ID — Mastercard BIN program ID
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._base_url = (base_url or os.environ.get("PAYMENTOLOGY_BASE_URL", "")).rstrip("/")
        self._api_key = api_key or os.environ.get("PAYMENTOLOGY_API_KEY", "")

        if not self._base_url:  # I-10: no fake integrations
            raise NotImplementedError(
                "PAYMENTOLOGY_BASE_URL not configured. "
                "Set up Paymentology sandbox (Sprint 10) and set env var."
            )

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    async def issue_card(self, request: CardIssueRequest) -> CardIssueResult:
        payload = {
            "customerId": request.customer_id,
            "programId": request.program_id,
            "cardholderName": request.cardholder_name,
            "currency": request.currency,
            "metadata": request.metadata or {},
        }
        async with httpx.AsyncClient(headers=self._headers) as client:
            resp = client.post(f"{self._base_url}/cards", json=payload)
            resp.raise_for_status()
            data = resp.json()
        return CardIssueResult(
            card_id=data["cardId"],
            masked_pan=data["maskedPan"],  # last 4 only — never full PAN
            status=_CARD_STATUS_MAP.get(data.get("status", "PENDING"), CardStatus.PENDING),
            expiry_month=data["expiryMonth"],
            expiry_year=data["expiryYear"],
        )

    async def authorise_transaction(self, request: AuthorisationRequest) -> AuthorisationResponse:
        """Companion API authorisation — BANXE decides approve/decline."""
        # This method is called BY Paymentology webhook, not to Paymentology.
        # Logic: check compliance + balance via auth_engine.
        # Stub — real logic in authorization/auth_engine.py
        raise NotImplementedError(
            "authorise_transaction must be wired to auth_engine.AuthEngine. "
            "See src/authorization/auth_engine.py."
        )

    async def suspend_card(self, card_id: str, reason: str) -> CardStatus:
        async with httpx.AsyncClient(headers=self._headers) as client:
            resp = client.patch(
                f"{self._base_url}/cards/{card_id}",
                json={"status": "SUSPENDED", "reason": reason},
            )
            resp.raise_for_status()
        return CardStatus.SUSPENDED

    async def cancel_card(self, card_id: str, reason: str) -> CardStatus:
        async with httpx.AsyncClient(headers=self._headers) as client:
            resp = client.patch(
                f"{self._base_url}/cards/{card_id}",
                json={"status": "CANCELLED", "reason": reason},
            )
            resp.raise_for_status()
        return CardStatus.CANCELLED

    async def get_card_status(self, card_id: str) -> CardStatus:
        async with httpx.AsyncClient(headers=self._headers) as client:
            resp = client.get(f"{self._base_url}/cards/{card_id}")
            resp.raise_for_status()
            data = resp.json()
        return _CARD_STATUS_MAP.get(data.get("status", "PENDING"), CardStatus.PENDING)
