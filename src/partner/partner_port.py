"""PartnerPort — abstract interface for external payment-partner operations.

WHY: ADR-021 five-new-ports (PartnerPort entry); ADR-027 audit trail.
All partner-facing payment initiation, status polling, and reconciliation
operations are routed through PartnerPort so that the three adapter
families (OpenBanking, SEPA, BaaS) are replaceable without touching
business logic.  Regulatory anchors: R-REG-01 (transaction record-keeping),
R-COMP-FCA-02 (CASS 15 reconciliation obligation).
"""

from __future__ import annotations

import abc
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

Currency = str  # ISO 4217, e.g. "EUR"
Iban = str  # IBAN string, e.g. "GB29NWBK60161331926819"
Amount = str  # Decimal string, NEVER float, e.g. "100.00"


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class PartnerType(StrEnum):
    OPEN_BANKING = "open_banking"
    SEPA = "sepa"
    BAAS = "baas"
    CARD_ACQUIRING = "card_acquiring"


class PaymentStatus(StrEnum):
    ACCEPTED = "accepted"
    SETTLED = "settled"
    REJECTED = "rejected"
    PENDING = "pending"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Value types (dataclasses)
# ---------------------------------------------------------------------------


@dataclass
class PartnerAccountId:
    partner: PartnerType
    external_account_id: str


@dataclass
class PaymentInstruction:
    from_account: PartnerAccountId
    to_iban: Iban
    amount: Amount
    currency: Currency
    reference: str
    idempotency_key: str
    correlation_id: str


@dataclass
class PaymentResult:
    status: PaymentStatus
    partner_tx_id: str | None = None
    settled_at: str | None = None
    reason: str | None = None
    raw: Mapping[str, Any] | None = None


@dataclass
class MismatchDetail:
    partner_tx_id: str
    expected: Amount
    actual: Amount


@dataclass
class ReconcileResult:
    date: str
    count: int
    mismatches: int
    mismatch_details: list[MismatchDetail]


# ---------------------------------------------------------------------------
# Error taxonomy
# ---------------------------------------------------------------------------


class PartnerPortError(Exception):
    """Base error for all PartnerPort operations.

    All errors carry an optional correlation_id to allow end-to-end tracing
    across service boundaries (ADR-027).
    """

    def __init__(self, message: str, *, correlation_id: str | None = None) -> None:
        super().__init__(message)
        self.correlation_id = correlation_id


class ValidationError(PartnerPortError):
    """Raised when the PaymentInstruction fails field-level or business validation
    before being submitted to the partner (e.g. invalid IBAN, zero amount).
    """


class IdempotencyConflict(PartnerPortError):
    """Raised when a duplicate idempotency_key is received with a payload that
    differs from the original request, indicating a caller error.
    """


class PartnerUnavailable(PartnerPortError):
    """Raised when the upstream partner API is unreachable or returns a
    transient server-side error; callers should retry with back-off.
    """


class InsufficientFunds(PartnerPortError):
    """Raised when the source account lacks sufficient balance to cover the
    requested payment amount including any partner fees.
    """


class ComplianceBlock(PartnerPortError):
    """Raised when the payment is blocked by a compliance or sanctions check
    (R-COMP-FCA-02).  Must not be retried without manual compliance review.
    """


class UnknownPartnerState(PartnerPortError):
    """Raised when the partner returns a status that cannot be mapped to a
    known PaymentStatus, requiring manual investigation.
    """


# ---------------------------------------------------------------------------
# Abstract port
# ---------------------------------------------------------------------------


class PartnerPort(abc.ABC):
    """Abstract port for external payment-partner operations (ADR-021, ADR-027,
    R-REG-01, R-COMP-FCA-02).

    Primary implementations injected at runtime: OpenBankingAdapter,
    SepaAdapter, BaasAdapter (Phase B/C — out of scope here).
    """

    @abc.abstractmethod
    async def initiate_payment(self, instruction: PaymentInstruction) -> PaymentResult:
        """Submit a payment instruction to the partner and return the initial result.

        Idempotent on instruction.idempotency_key: a second call with the same
        key and identical payload must return the original PaymentResult without
        re-submitting.  A conflicting payload raises IdempotencyConflict.
        """

    @abc.abstractmethod
    async def get_payment_status(self, partner_tx_id: str) -> PaymentResult:
        """Poll the partner for the current status of a previously initiated payment.

        Read-only and free of side effects; safe to call repeatedly for polling.
        """

    @abc.abstractmethod
    async def reconcile(self, date: str) -> ReconcileResult:
        """Compare the partner ledger against the midaz-ledger for the given date.

        Feeds the banxe-recon pipeline and satisfies R-REG-01 / CASS 15
        record-keeping obligations.  date must be an ISO 8601 calendar date
        string (YYYY-MM-DD).
        """
