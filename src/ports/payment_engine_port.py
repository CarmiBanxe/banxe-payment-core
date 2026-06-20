"""PaymentEnginePort — advisory payment-intent / status state-machine (MIG-M2.1).

WHY: cross-context migration M2 (BANXE.RAR -> EMI). This is the **advisory payments-engine
orchestration surface** (payment-intent lifecycle + status state-machine). It is DISTINCT from:
  - PaymentSwitchPort (Hyperswitch live execution) — NOT called here;
  - LedgerPort (Midaz live ledger/balances, ADR-013) — NOT called here;
  - any FeeEnginePort — NOT called here.

It is a **projection-consumer** over the accounts SoT (MIG-M2.2, banxe-emi-stack): it references
accounts by ``account_ref`` only — it holds NO balances and is NEVER a second balance store
(MIG-M1.3). Idempotency-key contract + status-consistency surface; advisory / read-only; **no live
execution, no fund movement** (operator-gated, ADR-103 PART 2). I-05: amounts are int minor units,
never float.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class PaymentIntentState(Enum):
    """Advisory payment-intent state-machine (no live execution)."""

    CREATED = "created"
    VALIDATED = "validated"
    AUTHORIZED = "authorized"
    SETTLED = "settled"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Allowed advisory state transitions (state-machine surface; no live side effects).
INTENT_TRANSITIONS: dict[PaymentIntentState, tuple[PaymentIntentState, ...]] = {
    PaymentIntentState.CREATED: (PaymentIntentState.VALIDATED, PaymentIntentState.CANCELLED),
    PaymentIntentState.VALIDATED: (
        PaymentIntentState.AUTHORIZED,
        PaymentIntentState.FAILED,
        PaymentIntentState.CANCELLED,
    ),
    PaymentIntentState.AUTHORIZED: (PaymentIntentState.SETTLED, PaymentIntentState.FAILED),
    PaymentIntentState.SETTLED: (),
    PaymentIntentState.FAILED: (),
    PaymentIntentState.CANCELLED: (),
}


@dataclass(frozen=True)
class PaymentIntent:
    """Advisory payment intent (projection over accounts SoT by ref; no balance held).

    amount_minor: int minor units (pence/cents), never float (I-05).
    """

    intent_id: str
    idempotency_key: str
    debtor_account_ref: str  # reference into accounts SoT (MIG-M2.2) — NOT a balance
    creditor_account_ref: str
    amount_minor: int
    currency: str  # ISO 4217
    state: PaymentIntentState = PaymentIntentState.CREATED
    source: str = "sandbox-mock"


@dataclass
class PaymentStatusRecord:
    """Advisory status record for an intent (state-consistency surface; reference-only)."""

    intent_id: str
    state: PaymentIntentState
    source: str = "sandbox-mock"
    history: list[PaymentIntentState] = field(default_factory=list)


class PaymentEnginePort(ABC):
    """Read-only advisory payments-engine contract (no live execution; idempotent intents)."""

    @abstractmethod
    def create_intent(
        self,
        *,
        idempotency_key: str,
        debtor_account_ref: str,
        creditor_account_ref: str,
        amount_minor: int,
        currency: str,
    ) -> PaymentIntent:
        """Create (or idempotently return) a payment intent. No live execution / fund movement."""

    @abstractmethod
    def get_status(self, intent_id: str) -> PaymentStatusRecord | None:
        """Return the advisory status of an intent, or None if unknown (fail-closed)."""

    @abstractmethod
    def list_intents(self) -> list[PaymentIntent]:
        """List known advisory intents (read-only)."""
