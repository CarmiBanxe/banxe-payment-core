"""Shared lineage & cost primitives for the L2 client-facing agents (canonical).

WHY: the Payments, FX/Exchange, and Wallet masks
(``src/agents/payments_agent.py``, ``fx_exchange_agent.py``, ``wallet_agent.py``)
each enforce the *same* governance vocabulary — the ADR-048 process handle, the
ADR-047 cost dimensions, and the ADR-046 decision-lineage record. Those
definitions were byte-for-byte identical across the three modules (DRY debt).
This module is the single canonical home for them; each mask module imports
from here and keeps only its mask-specific types (its mask config, its intent
vocabulary, its private evaluation context). Moving — not re-inventing — these
primitives preserves behaviour exactly: same gate inputs, same record schema.

Scope boundary (unchanged): this is pure governance data + the recorder seam.
The ClickHouse/lineage sink and the LLM-orchestration/routing layer
(``AGENT_ROUTING_ENABLED``) remain out of scope (Terminal A infra, ADR-049
§D6/§D7); the agents depend only on the :class:`DecisionRecorder` interface.

R-SEC (R-SEC-NEW-01, ADR-021): :class:`AgentDecisionRecord` carries opaque
metadata ONLY — never seed/entropy/key/password/plaintext/ciphertext. Secret
material lives solely on the per-mask intent fields, is routed straight to the
injected port, and (for results) is returned on ``AgentOutcome.result`` to the
caller, never recorded.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from threading import Lock
from typing import Protocol

# ---------------------------------------------------------------------------
# Shared mask vocabulary (ADR-046 / ADR-047 / ADR-049 §D4)
# ---------------------------------------------------------------------------


class ConfirmationDecision(StrEnum):
    """HITL band selected by the confirmation_policy (ADR-047 / ADR-049 §D4)."""

    AUTO = "auto"
    REVIEW = "review"
    BLOCK = "block"


class ComplianceResult(StrEnum):
    """Net L3 compliance-gate outcome carried on the lineage record (ADR-046)."""

    PASS = "PASS"  # nosec B105 — enum value, not a credential
    FAIL = "FAIL"
    ESCALATE = "ESCALATE"
    NA = "N/A"


class BudgetBreach(StrEnum):
    """Cost-cap breach flag for the lineage record (ADR-047 §D2/§D4)."""

    NONE = "NONE"
    WARN = "WARN"
    BREACH = "BREACH"


# ---------------------------------------------------------------------------
# Value types — ADR-048 process handle + ADR-047 cost dimensions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcessRef:
    """ADR-048 intent→process handle. Both fields required for a resolved intent."""

    process_id: str
    version: str

    @property
    def resolved(self) -> bool:
        return bool(self.process_id) and bool(self.version)


@dataclass(frozen=True)
class RequestCost:
    """Estimated cost of a single agent invocation (ADR-047 per-request dimensions)."""

    tokens: int
    cost: Decimal


@dataclass(frozen=True)
class CostCap:
    """Hard caps in both token and monetary (Decimal) dimensions (ADR-047 §D2)."""

    max_request_tokens: int
    max_request_cost: Decimal
    max_window_tokens: int
    max_window_cost: Decimal


@dataclass
class CostWindow:
    """Rolling per-window usage accumulator (ADR-047 §D2 per-window budget).

    ``window_ref`` defaults to a generic label; each agent overrides it with its
    own ``f"{mask.agent_id}:default"`` at construction (behaviour unchanged).

    Concurrency (S5.3): once the S5.1 L1 router dispatches concurrent live intents,
    one agent+window is shared across actions that execute at the same time. The
    accumulator's read-modify-write (``used += cost``) is therefore guarded by a
    ``threading.Lock`` so no update is lost — a lost update would under-count usage
    and let the per-window cap be silently bypassed (ADR-047). ``add`` stays
    synchronous so the agent call sites are unchanged; the lock is uncontended for
    a single action, so single-action behaviour is preserved exactly."""

    used_tokens: int = 0
    used_cost: Decimal = Decimal("0")
    window_ref: str = "agent:default"
    # Guards the accumulate below. Excluded from repr/eq: a lock has no value
    # identity and never participates in window equality or display.
    _lock: Lock = field(default_factory=Lock, repr=False, compare=False)

    def add(self, cost: RequestCost) -> None:
        """Atomically fold one request's cost into the running window totals.

        The whole read-modify-write is one critical section: under concurrent
        execution the lock serialises it so ``used_tokens``/``used_cost`` always
        equal the exact sum of every action's cost (no lost updates)."""
        with self._lock:
            self.used_tokens += cost.tokens
            self.used_cost += cost.cost


# ---------------------------------------------------------------------------
# Lineage record (ADR-046) + outcome
# ---------------------------------------------------------------------------


@dataclass
class AgentDecisionRecord:
    """Decision-lineage record emitted per action (ADR-046 schema + ADR-047 cost).

    R-SEC: carries opaque metadata ONLY — never seed/entropy/key/password/plaintext.
    """

    record_id: str
    timestamp: datetime
    agent_id: str
    triggering_event: str
    intent: str
    policies_evaluated: list[str]
    compliance_result: ComplianceResult
    reasoning_summary: str
    confidence_score: float
    action_taken: str
    human_reviewed_by: str | None
    correlation_id: str
    # ADR-047 cost lineage (cost is a first-class lineage dimension).
    cost_tokens: int = 0
    cost_amount: Decimal = Decimal("0")
    budget_window_ref: str = ""
    budget_breach_flag: BudgetBreach = BudgetBreach.NONE
    # ADR-046 §D5 additive fields (non-breaking; all default None when not supplied).
    # ``immutable_storage_ref`` is the WORM/immutable storage handle for the record.
    # ``input_tokens`` + ``output_tokens`` REFINE the existing ``cost_tokens`` total
    # into the prompt/completion split; the ``cost_tokens`` total stays authoritative.
    immutable_storage_ref: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class AgentOutcome:
    """Result of a masked action: the decision, whether a port was called, and the
    lineage record that was emitted (always non-None — lineage is non-optional).

    ``result`` carries the port's return value to the caller (which MAY be secret
    material, e.g. a generated mnemonic or decrypted plaintext); it is the
    functional return only and is NEVER part of the recorded lineage (R-SEC)."""

    decision: ConfirmationDecision
    executed: bool
    record: AgentDecisionRecord
    result: object | None = None
    halt_reason: str | None = None
    requires_step_up: bool = False
    requires_hitl: bool = False


class DecisionRecorder(ABC):
    """Sink for :class:`AgentDecisionRecord` (ADR-046 producer→sink seam).

    Injected, not implemented here: the ClickHouse/lineage wiring is out of scope
    (ADR-049 §D7). The agent depends only on this interface.
    """

    @abstractmethod
    async def record(self, record: AgentDecisionRecord) -> None:
        """Persist one decision-lineage record. Must be durable before the action
        is considered complete (ADR-046 §D4 producer obligation)."""


# ---------------------------------------------------------------------------
# Idempotent replay protection (ADR-046 / ADR-021) — money-movement seam
# ---------------------------------------------------------------------------


class IdempotencyStorePort(Protocol):
    """Replay-protection seam for money-movement actions (S5.3, DOUBLE-PAYMENT class).

    A money-movement agent records each ``idempotency_key`` it has *successfully*
    executed; a later replay of the same key is suppressed instead of re-executed,
    so a retried/double-submitted intent cannot move funds twice. Injected as an
    interface: the :class:`InMemoryIdempotencyStore` below is the single-process /
    test default; a distributed deployment wires a durable shared store (e.g. Redis)
    at the composition root.

    R-SEC: this port caches only the money-movement *result reference* (an opaque
    partner transaction handle), never key material. The secret-bearing Wallet
    contour does not use this port; payment confirmations are not secrets (ADR-021).
    """

    async def has_seen(self, key: str) -> bool:  # pragma: no cover - protocol stub
        """True if ``key`` was already executed to completion (a replay)."""
        ...

    async def mark_seen(self, key: str, result_ref: object) -> None:  # pragma: no cover
        """Record ``key`` as executed, keeping ``result_ref`` for replay returns.

        Called ONLY after a successful execution: a halted/failed action never
        marks its key, so a legitimate retry of an unfinished payment still runs."""
        ...

    async def prior_result(self, key: str) -> object | None:  # pragma: no cover
        """Return the result reference recorded for ``key``, or ``None`` if unseen."""
        ...


class InMemoryIdempotencyStore:
    """Default in-process :class:`IdempotencyStorePort` (single-process / tests).

    Maps an ``idempotency_key`` to the prior action's result reference so a replay
    returns it without re-executing. Each individual dict operation is atomic under
    the GIL; a distributed deployment swaps in a shared durable store at the
    composition root without touching the agent."""

    def __init__(self) -> None:
        self._seen: dict[str, object] = {}

    async def has_seen(self, key: str) -> bool:
        return key in self._seen

    async def mark_seen(self, key: str, result_ref: object) -> None:
        self._seen[key] = result_ref

    async def prior_result(self, key: str) -> object | None:
        return self._seen.get(key)


__all__ = [
    "AgentDecisionRecord",
    "AgentOutcome",
    "BudgetBreach",
    "ComplianceResult",
    "ConfirmationDecision",
    "CostCap",
    "CostWindow",
    "DecisionRecorder",
    "IdempotencyStorePort",
    "InMemoryIdempotencyStore",
    "ProcessRef",
    "RequestCost",
]
