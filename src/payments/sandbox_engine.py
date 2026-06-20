"""SandboxPaymentEngine — config-as-data advisory payments-engine (MIG-M2.1).

Sandbox/mock implementation of PaymentEnginePort. Idempotency-key contract: the same key returns the
**same** intent (no duplicate). Projection over accounts SoT by ``account_ref`` — holds NO balances,
NEVER a second balance store. Does NOT call PaymentSwitchPort / LedgerPort / FeeEnginePort; **no live
execution, no fund movement** (operator-gated). I-05: amount_minor int, never float.
"""

from src.ports.payment_engine_port import (
    PaymentEnginePort,
    PaymentIntent,
    PaymentIntentState,
    PaymentStatusRecord,
)

SANDBOX_SOURCE = "sandbox-mock"


class SandboxPaymentEngine(PaymentEnginePort):
    """In-memory advisory engine; idempotent by idempotency_key; no live side effects."""

    def __init__(self) -> None:
        self._by_key: dict[str, PaymentIntent] = {}
        self._status: dict[str, PaymentStatusRecord] = {}

    def create_intent(
        self,
        *,
        idempotency_key: str,
        debtor_account_ref: str,
        creditor_account_ref: str,
        amount_minor: int,
        currency: str,
    ) -> PaymentIntent:
        if not isinstance(amount_minor, int):  # I-05: int minor units only, never float
            raise TypeError("amount_minor must be int (minor units)")
        # idempotency: same key -> same intent, no duplicate
        existing = self._by_key.get(idempotency_key)
        if existing is not None:
            return existing
        intent = PaymentIntent(
            intent_id=f"PI-{len(self._by_key) + 1:06d}",
            idempotency_key=idempotency_key,
            debtor_account_ref=debtor_account_ref,
            creditor_account_ref=creditor_account_ref,
            amount_minor=amount_minor,
            currency=currency,
            state=PaymentIntentState.CREATED,
            source=SANDBOX_SOURCE,
        )
        self._by_key[idempotency_key] = intent
        self._status[intent.intent_id] = PaymentStatusRecord(
            intent_id=intent.intent_id,
            state=PaymentIntentState.CREATED,
            source=SANDBOX_SOURCE,
            history=[PaymentIntentState.CREATED],
        )
        return intent

    def get_status(self, intent_id: str) -> PaymentStatusRecord | None:
        return self._status.get(intent_id)  # fail-closed: None if unknown

    def list_intents(self) -> list[PaymentIntent]:
        return list(self._by_key.values())
