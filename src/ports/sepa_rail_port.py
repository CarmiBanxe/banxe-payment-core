"""SepaRailPort — advisory SEPA outgoing rail-consumer state-machine (MIG-M2.6).

WHY: cross-context migration M2 (BANXE.RAR -> EMI), first clean SEPA slice per MIG-M1.5 (SEPA
outgoing core: sepa-accounts + Papaya/SWIFT outgoing). This is the **advisory SEPA-rail consumer**
ON TOP of the payments engine (MIG-M2.1, PaymentEnginePort): it references a payment intent + accounts
by ref and tracks the **SEPA outgoing rail state-machine** + status reconciliation. It is DISTINCT
from / does NOT call:
  - PaymentEnginePort live execution (it *consumes* an intent, advisory only);
  - PaymentSwitchPort (Hyperswitch), LedgerPort (Midaz, ADR-013), FeeEnginePort;
  - the live Papaya / SWIFT rails (no real rail call / file dispatch / webhook publish).

**Projection-consumer** over the accounts SoT (MIG-M2.2) by ``account_ref`` only — holds NO balances,
never a second balance store (MIG-M1.3). Webhook/file idempotency contract + status reconciliation
surface. Advisory / read-only; **no live SEPA execution, no fund movement** (operator-gated, ADR-103
PART 2). I-05: amounts int minor units, never float. sumsub/KYC carve-out (MIG-M1.4/M1.5) out of scope.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class SepaRailState(Enum):
    """Advisory SEPA outgoing rail state-machine (no live rail call)."""

    SUBMITTED = "submitted"
    PACS_GENERATED = "pacs_generated"  # pacs.008 outgoing message built (advisory; not sent)
    SENT_TO_RAIL = "sent_to_rail"
    ACK = "ack"
    SETTLED = "settled"
    RETURNED = "returned"
    REJECTED = "rejected"


# Allowed advisory rail transitions (state-machine surface; no live side effects).
RAIL_TRANSITIONS: dict[SepaRailState, tuple[SepaRailState, ...]] = {
    SepaRailState.SUBMITTED: (SepaRailState.PACS_GENERATED, SepaRailState.REJECTED),
    SepaRailState.PACS_GENERATED: (SepaRailState.SENT_TO_RAIL, SepaRailState.REJECTED),
    SepaRailState.SENT_TO_RAIL: (SepaRailState.ACK, SepaRailState.REJECTED),
    SepaRailState.ACK: (SepaRailState.SETTLED, SepaRailState.RETURNED),
    SepaRailState.SETTLED: (),
    SepaRailState.RETURNED: (),
    SepaRailState.REJECTED: (),
}


@dataclass(frozen=True)
class SepaOutgoingIntent:
    """Advisory SEPA outgoing rail intent (consumes a payment intent; projection over accounts SoT).

    amount_minor: int minor units, never float (I-05). account refs are projections — no balance.
    """

    rail_intent_id: str
    file_dedup_key: str  # webhook/file idempotency key
    payment_intent_id: str  # consumed from PaymentEnginePort (MIG-M2.1)
    debtor_account_ref: str  # projection into accounts SoT (MIG-M2.2) — NOT a balance
    creditor_account_ref: str
    creditor_iban: str
    amount_minor: int
    currency: str  # ISO 4217 (SEPA: EUR)
    state: SepaRailState = SepaRailState.SUBMITTED
    source: str = "sandbox-mock"


@dataclass
class SepaRailStatusRecord:
    """Advisory rail status record (status-reconciliation surface; reference-only)."""

    rail_intent_id: str
    state: SepaRailState
    source: str = "sandbox-mock"
    history: list[SepaRailState] = field(default_factory=list)


class SepaRailPort(ABC):
    """Read-only advisory SEPA outgoing rail-consumer contract (no live execution; idempotent files)."""

    @abstractmethod
    def submit_outgoing(
        self,
        *,
        file_dedup_key: str,
        payment_intent_id: str,
        creditor_iban: str,
    ) -> SepaOutgoingIntent:
        """Build (or idempotently return) an advisory SEPA outgoing rail intent from a payment intent.

        Consumes the payment intent (MIG-M2.1) for account refs + amount. No live rail dispatch / fund
        movement. Same file_dedup_key returns the same rail intent (webhook/file idempotency).
        """

    @abstractmethod
    def get_rail_status(self, rail_intent_id: str) -> SepaRailStatusRecord | None:
        """Advisory rail status, or None if unknown (fail-closed)."""

    @abstractmethod
    def list_outgoing(self) -> list[SepaOutgoingIntent]:
        """List known advisory SEPA outgoing rail intents (read-only)."""
