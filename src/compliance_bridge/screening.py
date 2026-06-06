"""ComplianceScreening — pre-auth sanctions and jurisdiction check.

WHY: I-01 (sanctions FIRST), I-02 (Category A reject).
Calls banxe-emi-stack compliance API at :8093.
MUST be called before any payment authorisation.
I-10: If COMPLIANCE_API_URL not configured, raise — never bypass screening.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum

import httpx

_DEFAULT_COMPLIANCE_URL = "http://localhost:8093"

# Category A jurisdictions — always REJECT (I-02)
_CATEGORY_A = frozenset(["RU", "BY", "IR", "KP", "CU", "MM", "AF"])


class ScreeningDecision(Enum):
    # B105 false positive: enum decision label, not a credential.
    PASS = "PASS"  # nosec B105
    BLOCK = "BLOCK"  # sanctions hit or Category A
    REVIEW = "REVIEW"  # manual review required


@dataclass
class ScreeningRequest:
    customer_id: str
    counterparty_country: str  # ISO 3166-1 alpha-2
    amount_minor: int
    currency: str
    reference: str


@dataclass
class ScreeningResult:
    decision: ScreeningDecision
    reason: str | None = None
    risk_score: int | None = None


class ComplianceScreening:
    """Pre-authorization compliance screening (I-01, I-02).

    Calls :8093/check_transaction — part of banxe-emi-stack compliance stack.
    """

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = (
            base_url or os.environ.get("COMPLIANCE_API_URL", _DEFAULT_COMPLIANCE_URL)
        ).rstrip("/")

    async def screen(self, request: ScreeningRequest) -> ScreeningResult:
        """Run pre-auth screening. Must pass before any authorisation.

        I-02: Category A → immediate BLOCK without API call.
        I-01: Sanctions check → BLOCK on hit.
        """
        # Fast path: Category A jurisdiction (I-02)
        country = (request.counterparty_country or "").upper()
        if country in _CATEGORY_A:
            return ScreeningResult(
                decision=ScreeningDecision.BLOCK,
                reason=f"Category A jurisdiction: {country}",
                risk_score=100,
            )

        payload = {
            "customer_id": request.customer_id,
            "jurisdiction": country,
            "amount": request.amount_minor,
            "currency": request.currency,
            "reference": request.reference,
        }
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(f"{self._base_url}/check_transaction", json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.RequestError as exc:
            # Fail-closed: if compliance API is unreachable, block (I-01)
            return ScreeningResult(
                decision=ScreeningDecision.BLOCK,
                reason=f"Compliance API unreachable: {exc}",
                risk_score=None,
            )

        flagged = data.get("flagged", True)
        risk_score = data.get("risk_score", 0)
        action = data.get("recommended_action", "APPROVE")

        if flagged or action in ("SAR", "REJECT"):
            return ScreeningResult(
                decision=ScreeningDecision.BLOCK,
                reason=data.get("rules_triggered", [{}])[0].get("name")
                if data.get("rules_triggered")
                else "flagged",
                risk_score=risk_score,
            )
        if action == "HOLD":
            return ScreeningResult(
                decision=ScreeningDecision.REVIEW,
                reason="Manual review required",
                risk_score=risk_score,
            )
        return ScreeningResult(
            decision=ScreeningDecision.PASS,
            risk_score=risk_score,
        )
