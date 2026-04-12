"""LedgerPort — abstract interface for ledger operations via Midaz.

WHY: ADR-013 (Midaz CBS), ADR-014 (Composable Stack), ADR-015.
Midaz is the PRIMARY ledger (Operational Dependency, replaceable via this port).
All balance queries and debit/credit operations go through LedgerPort.
I-05: amounts are always int (minor units) — never float.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LedgerEntry:
    account_id: str
    amount_minor: int  # positive = credit, negative = debit
    currency: str  # ISO 4217
    reference: str  # payment/settlement reference
    idempotency_key: str  # required for append-only log (I-24)
    metadata: dict | None = None


@dataclass
class BalanceResult:
    account_id: str
    available_minor: int  # available balance in minor units (I-05: int, not float)
    pending_minor: int  # pending (authorized, not yet settled)
    currency: str


class LedgerPort(ABC):
    """Abstract port for ledger (CBS) operations (ADR-013, ADR-014, I-20).

    Primary implementation: MidazAdapter (:8095).
    """

    @abstractmethod
    async def get_balance(self, account_id: str, currency: str) -> BalanceResult:
        """Query available and pending balance for an account."""

    @abstractmethod
    async def debit(self, entry: LedgerEntry) -> str:
        """Debit an account (decrease balance). Returns transaction_id."""

    @abstractmethod
    async def credit(self, entry: LedgerEntry) -> str:
        """Credit an account (increase balance). Returns transaction_id."""

    @abstractmethod
    async def reserve(self, entry: LedgerEntry) -> str:
        """Reserve (hold) funds for a pending authorization. Returns reservation_id."""

    @abstractmethod
    async def release_reservation(self, reservation_id: str) -> None:
        """Release a previously reserved hold (e.g., on void or expiry)."""

    @abstractmethod
    async def settle_reservation(
        self, reservation_id: str, actual_amount_minor: int | None = None
    ) -> str:
        """Convert a reservation to a final debit. Returns settlement_id."""
