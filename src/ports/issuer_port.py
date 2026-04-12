"""IssuerPort — abstract interface for card issuing operations.

WHY: ADR-015, ADR-011 (Reference vs Dependency).
Paymentology is an Operational Dependency (replaceable, I-20).
Companion API pattern: balance authorisation stays in Midaz (BANXE controls funds).
All upstream code calls IssuerPort, never Paymentology API directly.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class CardStatus(Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"
    PENDING = "pending"


class AuthorisationDecision(Enum):
    APPROVE = "APPROVE"
    DECLINE = "DECLINE"
    REFER = "REFER"  # send to HITL (I-04)


@dataclass
class CardIssueRequest:
    customer_id: str
    program_id: str  # Mastercard BIN program
    cardholder_name: str
    currency: str  # ISO 4217
    metadata: dict | None = None


@dataclass
class CardIssueResult:
    card_id: str
    masked_pan: str  # last 4 digits only — never full PAN
    status: CardStatus
    expiry_month: int
    expiry_year: int


@dataclass
class AuthorisationRequest:
    """Incoming card authorisation from Paymentology Companion API.

    WHY Companion API: balance check is done by BANXE (Midaz),
    not delegated to Paymentology. BANXE controls approve/decline.
    """

    card_id: str
    amount_minor: int  # never float (I-05)
    currency: str
    merchant_name: str
    merchant_category_code: str
    transaction_id: str
    idempotency_key: str


@dataclass
class AuthorisationResponse:
    decision: AuthorisationDecision
    transaction_id: str
    response_code: str  # ISO 8583 response code
    available_balance_minor: int | None = None


class IssuerPort(ABC):
    """Abstract port for card issuer operations (ADR-015, I-20).

    Implementations: PaymentologyAdapter (primary), MockIssuerAdapter (tests only).
    """

    @abstractmethod
    async def issue_card(self, request: CardIssueRequest) -> CardIssueResult:
        """Issue a new virtual or physical card to a customer."""

    @abstractmethod
    async def authorise_transaction(self, request: AuthorisationRequest) -> AuthorisationResponse:
        """Process an incoming card authorisation (Companion API callback).

        MUST check I-01 (sanctions) and I-02 (jurisdiction) before approve.
        Amounts ≥ £10,000 → REFER for HITL (I-04).
        """

    @abstractmethod
    async def suspend_card(self, card_id: str, reason: str) -> CardStatus:
        """Temporarily suspend a card (reversible). Log reason for I-24."""

    @abstractmethod
    async def cancel_card(self, card_id: str, reason: str) -> CardStatus:
        """Permanently cancel a card. Log reason for I-24."""

    @abstractmethod
    async def get_card_status(self, card_id: str) -> CardStatus:
        """Return current status of a card."""
