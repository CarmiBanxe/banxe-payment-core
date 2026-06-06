"""ExchangePort — abstract interface for crypto exchange / trading operations.

WHY: C6 trading capability per ADR-016 (trading-ui) and ADR-021 (ExchangePort
isolation).  All rate-fetching, order placement, cancellation, and status polling
are routed through ExchangePort so that the three adapter families
(PrimaryExchangeAdapter from legacy fast-exchange, CCXTFallbackAdapter, and
HyperswitchAdapter) are replaceable without touching business logic.

Idempotency anchor (ADR-021): place_order is financially consequential and MUST
be idempotent on client_order_id — adapters store (client_order_id -> order_id)
before calling the exchange and return the original OrderResult on replay.

Audit anchor (ADR-027 + R-COMP-FCA-02): every operation emits one
guardian_audit_events row carrying correlation_id, client_order_id, order_id,
operation, state, http_status, latency_ms, and timestamp_utc.  OrderResult.raw
is stored for MLRO and FCA Section 4 review; PII is redacted per ADR-021.
Retention: ≥90 days for all orders; 5 years for any order that moves client
money (CASS 15).

Risk register: R-SEC-NEW-03 (order regression), R-COMP-FCA-02 (order audit).
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

AssetSymbol = str  # e.g. "BTC", "ETH", "EUR"
Amount = str  # Decimal string, NEVER float — financial precision is mandatory


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"


class OrderState(StrEnum):
    ACCEPTED = "accepted"
    FILLED = "filled"
    PARTIAL = "partial"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Value types (dataclasses)
# ---------------------------------------------------------------------------


@dataclass
class RateQuote:
    """Spot rate snapshot for a currency pair.

    ttl_seconds is authoritative: adapters MUST refuse to trade on a quote
    whose age exceeds ttl_seconds (raise StaleRate instead of using stale data).
    Rate is sourced via crypto-ops-monitor RPC (SPEC #7 / ADR-021).
    """

    base_asset: AssetSymbol
    quote_asset: AssetSymbol
    bid: Amount
    ask: Amount
    ttl_seconds: int
    quoted_at: str  # ISO 8601 UTC timestamp


@dataclass
class OrderRequest:
    """Instruction to place an exchange order.

    client_order_id is the caller-generated UUID v4 idempotency key.  Adapters
    store this key BEFORE calling the exchange so that retries never double-execute.
    correlation_id links the order to the originating request trace (ADR-027).
    limit_price is required when type == OrderType.LIMIT; absent for MARKET orders.
    """

    base_asset: AssetSymbol
    quote_asset: AssetSymbol
    side: OrderSide
    type: OrderType
    amount: Amount
    client_order_id: str  # idempotency key, caller-generated UUID v4
    correlation_id: str
    limit_price: Amount | None = None


@dataclass
class OrderResult:
    """Outcome of an order placement or status query.

    raw holds the unmodified exchange response for MLRO and FCA Section 4 audit;
    PII within raw must be redacted by the adapter before persisting (ADR-021).
    average_price and fee are None for orders that have not yet filled.
    """

    order_id: str
    state: OrderState
    filled_amount: Amount
    average_price: Amount | None = None
    fee: Amount | None = None
    raw: Mapping[str, Any] | None = None


# ---------------------------------------------------------------------------
# Error taxonomy
# ---------------------------------------------------------------------------


class ExchangePortError(Exception):
    """Base error for all ExchangePort operations.

    Every error carries an optional correlation_id for end-to-end tracing
    (ADR-027) and an optional client_order_id so that audit records can be
    linked to the originating order intent even when the exchange never
    assigned an order_id.  Adapters persist all errors to guardian_audit_events.
    """

    def __init__(
        self,
        message: str,
        *,
        correlation_id: str | None = None,
        client_order_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.correlation_id = correlation_id
        self.client_order_id = client_order_id


class ValidationError(ExchangePortError):
    """Raised when OrderRequest or method arguments fail field-level or business
    validation before being submitted to the exchange (e.g. unknown symbol,
    non-positive amount, missing limit_price for LIMIT order).

    Caller action: fix the request and resubmit; do not retry unchanged.
    """


class IdempotencyConflict(ExchangePortError):
    """Raised when client_order_id was previously registered with a different
    OrderRequest payload, indicating a caller-side bug (key reuse across distinct
    order intents).

    Caller action: reject the request; investigate the caller; do not retry.
    """


class StaleRate(ExchangePortError):
    """Raised when a RateQuote is reused after its ttl_seconds window has expired.
    Adapters MUST refuse to place an order against a stale rate to prevent
    inadvertent trading at outdated prices (R-SEC-NEW-03).

    Caller action: call get_rate again to obtain a fresh quote, then retry.
    """


class ExchangeUnavailable(ExchangePortError):
    """Raised when the upstream exchange API is unreachable or returns a transient
    server-side error (timeout, 5xx).  Circuit-breaker failover from
    PrimaryExchangeAdapter to CCXTFallbackAdapter is managed externally via
    @banxe/circuit-breaker (SPEC #6).

    Caller action: retry with exponential back-off via @banxe/circuit-breaker.
    """


class InsufficientBalance(ExchangePortError):
    """Raised when the trading account lacks sufficient balance to cover the order
    amount and associated fees.

    Caller action: surface the error to the end user; do not retry automatically.
    """


class ComplianceBlock(ExchangePortError):
    """Raised when the order is blocked by a KYC tier limit, sanctions screening,
    or Travel Rule gate (R-COMP-FCA-02; KYCProviderPort SPEC #8).

    Caller action: escalate to MLRO for manual review; NEVER auto-retry.
    """


class PartialFillTimeout(ExchangePortError):
    """Raised when an order was partially filled and then expired or timed out
    before reaching full execution.

    Caller action: reconcile the partial fill; settle the filled portion; do not
    resubmit the original order without adjusting the amount.
    """


# ---------------------------------------------------------------------------
# Abstract port
# ---------------------------------------------------------------------------


class ExchangePort(abc.ABC):
    """Abstract port for crypto exchange / trading operations (ADR-016, ADR-021,
    ADR-027, R-SEC-NEW-03, R-COMP-FCA-02).

    Primary implementations injected at runtime: PrimaryExchangeAdapter (legacy
    fast-exchange), CCXTFallbackAdapter (CCXT Pro), HyperswitchAdapter — all
    three implement this identical interface.  Selection is governed by the
    EXCHANGE_PROVIDER config flag with circuit-breaker failover (SPEC #6).
    """

    @abc.abstractmethod
    async def get_rate(self, base: AssetSymbol, quote: AssetSymbol) -> RateQuote:
        """Fetch the current spot rate for the base/quote currency pair.

        Read-only and free of side effects.  The returned RateQuote.ttl_seconds
        MUST be honoured: callers must not reuse the quote after expiry, and
        adapters must raise StaleRate if a stale quote is detected at trade time.
        Rate data is sourced via the crypto-ops-monitor RPC service (SPEC #7).

        Emits one guardian_audit_events row (ADR-027).
        """

    @abc.abstractmethod
    async def place_order(self, order: OrderRequest) -> OrderResult:
        """Submit an order to the exchange and return the initial OrderResult.

        MUST be idempotent on order.client_order_id: the adapter stores
        (client_order_id -> order_id) BEFORE calling the exchange.  A retry with
        the same client_order_id and identical payload returns the stored
        OrderResult without re-placing.  A conflicting payload raises
        IdempotencyConflict.

        Idempotency retention: ≥90 days; 5 years for orders that move client
        money (CASS 15).  OrderResult.raw is persisted for MLRO + FCA Section 4
        audit with PII redacted per ADR-021.

        Emits one guardian_audit_events row (ADR-027).
        """

    @abc.abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Request cancellation of an open order.

        Idempotent: cancelling an order that is already in a final state
        (FILLED, REJECTED, EXPIRED, CANCELLED) returns False without raising.
        Returns True when the cancellation is accepted by the exchange.

        Emits one guardian_audit_events row (ADR-027).
        """

    @abc.abstractmethod
    async def get_order_status(self, order_id: str) -> OrderResult:
        """Fetch the current state of a previously placed order.

        Read-only and free of side effects; safe to call repeatedly for polling.

        Emits one guardian_audit_events row (ADR-027).
        """
