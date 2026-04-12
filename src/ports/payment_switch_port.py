"""PaymentSwitchPort — abstract interface for payment orchestration.

WHY: ADR-015, ADR-011 (Reference vs Dependency).
Hyperswitch is an Operational Dependency (replaceable).
All upstream code calls PaymentSwitchPort, never Hyperswitch directly.
FCA: payment processing must be auditable and controllable.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class PaymentStatus(Enum):
    AUTHORIZED = "authorized"
    DECLINED = "declined"
    PENDING_3DS = "pending_3ds"
    FAILED = "failed"
    CAPTURED = "captured"
    REFUNDED = "refunded"
    VOIDED = "voided"


@dataclass
class PaymentRequest:
    amount_minor: int  # amount in minor units (pence, cents) — never float (I-05)
    currency: str  # ISO 4217
    customer_id: str
    card_token: str  # tokenized, never raw PAN
    merchant_id: str
    idempotency_key: str  # required — I-24 append-only
    metadata: dict | None = None


@dataclass
class PaymentResult:
    status: PaymentStatus
    transaction_id: str
    processor_response_code: str
    amount_minor: int
    currency: str
    error_message: str | None = None


class PaymentSwitchPort(ABC):
    """Abstract port for payment switch operations (ADR-015, I-20).

    Implementations: HyperswitchAdapter (primary), MockSwitchAdapter (tests only).
    Never call Hyperswitch HTTP API directly — always use this port.
    """

    @abstractmethod
    async def authorize(self, request: PaymentRequest) -> PaymentResult:
        """Submit a payment authorization request.

        MUST be called AFTER compliance_bridge.screening passes (I-01).
        """

    @abstractmethod
    async def capture(self, transaction_id: str, amount_minor: int | None = None) -> PaymentResult:
        """Capture a previously authorized payment.

        Args:
            transaction_id: ID from a prior authorize() call.
            amount_minor: Partial capture amount; None = full capture.
        """

    @abstractmethod
    async def refund(self, transaction_id: str, amount_minor: int, reason: str) -> PaymentResult:
        """Refund a captured payment (full or partial).

        Args:
            amount_minor: Refund amount in minor units — must be ≤ captured amount.
            reason: Free-text reason for audit trail (I-24).
        """

    @abstractmethod
    async def void(self, transaction_id: str) -> PaymentResult:
        """Void an authorized but not yet captured payment."""

    @abstractmethod
    async def get_payment_status(self, transaction_id: str) -> PaymentResult:
        """Retrieve current status of a payment by transaction ID."""
