"""AuthEngine — balance check + compliance → approve/decline (ADR-015).

Flow:
  1. ComplianceScreening (I-01, I-02) — MUST be first
  2. Balance check via LedgerPort (Midaz)
  3. Amount threshold check (I-04: ≥£10,000 → HITL)
  4. Return AuthorisationResponse

WHY: BANXE controls the approve/decline decision — not delegated to Paymentology.
This is the Companion API pattern from ADR-015.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.compliance_bridge.screening import (
    ComplianceScreening,
    ScreeningDecision,
    ScreeningRequest,
)
from src.ports.issuer_port import (
    AuthorisationDecision,
    AuthorisationRequest,
    AuthorisationResponse,
)
from src.ports.ledger_port import LedgerPort

# I-04: amounts ≥ this threshold require HITL
_HITL_THRESHOLD_MINOR = 1_000_000  # £10,000 in pence

# ISO 8583 response codes
_RC_APPROVE = "00"
_RC_DECLINE = "05"
_RC_REFER = "01"
_RC_INSUFFICIENT = "51"


@dataclass
class AuthEngineResult:
    decision: AuthorisationDecision
    response_code: str
    reason: str
    available_balance_minor: int | None = None


class AuthEngine:
    """Core authorisation engine — compliance + balance → approve/decline.

    Wired into PaymentologyAdapter.authorise_transaction() (Companion API).
    """

    def __init__(
        self,
        ledger: LedgerPort,
        screening: ComplianceScreening | None = None,
    ) -> None:
        self._ledger = ledger
        self._screening = screening or ComplianceScreening()

    async def authorise(self, request: AuthorisationRequest) -> AuthorisationResponse:
        """Process a card authorisation request end-to-end."""

        # Step 1: Compliance screening (I-01 — MUST be first)
        screen_req = ScreeningRequest(
            customer_id=request.card_id,  # card → customer mapping in production
            counterparty_country="GB",  # derive from merchant in production
            amount_minor=request.amount_minor,
            currency=request.currency,
            reference=request.transaction_id,
        )
        screen_result = await self._screening.screen(screen_req)
        if screen_result.decision == ScreeningDecision.BLOCK:
            return AuthorisationResponse(
                decision=AuthorisationDecision.DECLINE,
                transaction_id=request.transaction_id,
                response_code=_RC_DECLINE,
            )
        if screen_result.decision == ScreeningDecision.REVIEW:
            return AuthorisationResponse(
                decision=AuthorisationDecision.REFER,
                transaction_id=request.transaction_id,
                response_code=_RC_REFER,
            )

        # Step 2: HITL threshold check (I-04)
        if request.amount_minor >= _HITL_THRESHOLD_MINOR:
            return AuthorisationResponse(
                decision=AuthorisationDecision.REFER,
                transaction_id=request.transaction_id,
                response_code=_RC_REFER,
            )

        # Step 3: Balance check via Midaz (LedgerPort)
        balance = await self._ledger.get_balance(
            account_id=request.card_id, currency=request.currency
        )
        if balance.available_minor < request.amount_minor:
            return AuthorisationResponse(
                decision=AuthorisationDecision.DECLINE,
                transaction_id=request.transaction_id,
                response_code=_RC_INSUFFICIENT,
                available_balance_minor=balance.available_minor,
            )

        # Step 4: Approve
        return AuthorisationResponse(
            decision=AuthorisationDecision.APPROVE,
            transaction_id=request.transaction_id,
            response_code=_RC_APPROVE,
            available_balance_minor=balance.available_minor - request.amount_minor,
        )
