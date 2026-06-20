"""SandboxSepaRail — config-as-data advisory SEPA outgoing rail-consumer (MIG-M2.6).

Sandbox/mock impl of SepaRailPort. CONSUMES PaymentEnginePort (MIG-M2.1): reads a payment intent for
account refs + amount, then tracks the advisory SEPA rail state-machine. Projection over accounts SoT
by ``account_ref`` — holds NO balances, never a second balance store. Webhook/file idempotency: the
same file_dedup_key returns the same rail intent (no duplicate). Status-reconciliation surface only —
does NOT call the live Papaya / SWIFT rails, PaymentSwitchPort, LedgerPort (Midaz) or FeeEnginePort;
**no live SEPA execution, no fund movement** (operator-gated). I-05: amount_minor int, never float.
"""

from src.ports.payment_engine_port import PaymentEnginePort
from src.ports.sepa_rail_port import (
    SepaOutgoingIntent,
    SepaRailPort,
    SepaRailState,
    SepaRailStatusRecord,
)

SANDBOX_SOURCE = "sandbox-mock"


class SandboxSepaRail(SepaRailPort):
    """In-memory advisory SEPA rail; consumes a PaymentEnginePort; idempotent by file_dedup_key."""

    def __init__(self, engine: PaymentEnginePort) -> None:
        self._engine = engine
        self._by_key: dict[str, SepaOutgoingIntent] = {}
        self._status: dict[str, SepaRailStatusRecord] = {}

    def submit_outgoing(
        self,
        *,
        file_dedup_key: str,
        payment_intent_id: str,
        creditor_iban: str,
    ) -> SepaOutgoingIntent:
        # webhook/file idempotency: same key -> same rail intent, no duplicate
        existing = self._by_key.get(file_dedup_key)
        if existing is not None:
            return existing
        # consume the payment intent (MIG-M2.1) for account refs + amount (projection; no balance)
        intent = next(
            (i for i in self._engine.list_intents() if i.intent_id == payment_intent_id), None
        )
        if intent is None:
            raise KeyError(f"unknown payment_intent_id: {payment_intent_id}")  # fail-closed
        if not isinstance(intent.amount_minor, int):  # I-05 guard
            raise TypeError("amount_minor must be int (minor units)")
        rail = SepaOutgoingIntent(
            rail_intent_id=f"SEPA-OUT-{len(self._by_key) + 1:06d}",
            file_dedup_key=file_dedup_key,
            payment_intent_id=payment_intent_id,
            debtor_account_ref=intent.debtor_account_ref,
            creditor_account_ref=intent.creditor_account_ref,
            creditor_iban=creditor_iban,
            amount_minor=intent.amount_minor,
            currency=intent.currency,
            state=SepaRailState.SUBMITTED,
            source=SANDBOX_SOURCE,
        )
        self._by_key[file_dedup_key] = rail
        self._status[rail.rail_intent_id] = SepaRailStatusRecord(
            rail_intent_id=rail.rail_intent_id,
            state=SepaRailState.SUBMITTED,
            source=SANDBOX_SOURCE,
            history=[SepaRailState.SUBMITTED],
        )
        return rail

    def get_rail_status(self, rail_intent_id: str) -> SepaRailStatusRecord | None:
        return self._status.get(rail_intent_id)  # fail-closed: None if unknown

    def list_outgoing(self) -> list[SepaOutgoingIntent]:
        return list(self._by_key.values())
