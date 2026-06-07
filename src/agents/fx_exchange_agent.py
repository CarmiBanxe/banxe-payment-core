"""FXExchangeAgent — L2 client-facing FX/Exchange agent (ADR-049 FX mask).

WHY: ADR-049 (Intent Layer & Client-Facing Agent Masks) specifies the
client-facing **FX/Exchange mask**: the governed surface through which a
resolved client intent becomes a bounded exchange action. This module mirrors
the Payments mask (``src/agents/payments_agent.py``) for the trading contour.
It implements the agent *logic* and *governance enforcement* of the FX mask; it
does NOT implement the ports, the LLM-orchestration/routing layer
(``AGENT_ROUTING_ENABLED`` stays out of scope — Terminal A infra, ADR-049
§D6/§D7), or the ClickHouse sink.

INCREMENT 1 — AUTO read/quote path ONLY (``ExchangePort.get_rate``); NO money
movement. ``place_order`` / ``cancel_order``, the REVIEW flow, biometric
step-up execution, and the ``WalletPort`` settlement leg are DEFERRED to
increment 2 (see the TODO markers below).

The FX mask (ADR-049 §D3) enforced here, in §D2 chain order:

* ``scope``                — ExchangePort (+ WalletPort, increment 2) operations
                             on an allow-list; both ports are injected, never
                             implemented here. An op not on the allow-list is rejected.
* ``autonomy_level``       — AUTO-biased for the read/quote path (ADR-049 §D3);
                             money movement is REVIEW-biased in increment 2.
* ``confirmation_policy``  — AUTO > 0.90 / REVIEW 0.70–0.90 / BLOCK < 0.70
                             (ADR-047 thresholds, ADR-049 §D4). In increment 1
                             only the AUTO band executes; below AUTO halts.
* ``cost_cap``             — per-request AND per-window hard caps, token AND
                             monetary (Decimal) dimensions (ADR-047 §D2, ADR-049 §D3).
* ``lineage_obligation``   — one ``AgentDecisionRecord`` per action (ADR-046), non-optional.
* ``compliance_gate``      — AML + sanctions + Travel-Rule contour (Ruflo mandatory,
                             L3); a non-PASS result halts (ADR-049 §D3/§D4).

Any one of {unresolved process_ref, out-of-scope op, below-AUTO confidence,
cost-cap breach, compliance fail, missing step-up} halts the action (ADR-049
§D4 — independent halt conditions). Mask *values* (caps, thresholds, scope,
gate) are config-as-data (CLAUDE.md §10), carried on :class:`FXExchangeMask`,
never hardcoded in flow logic.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from src.exchangeport.exchange_port import (
    AssetSymbol,
    ComplianceBlock,
    ExchangePort,
    ExchangePortError,
)
from src.wallet.wallet_port import WalletPort

# ---------------------------------------------------------------------------
# Mask vocabulary
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


class AutonomyLevel(StrEnum):
    """Mask autonomy posture (ADR-049 §D3). Read/quote is AUTO-biased; money
    movement is REVIEW-biased (increment 2)."""

    AUTO_BIASED = "auto_biased"
    REVIEW_BIASED = "review_biased"


# ---------------------------------------------------------------------------
# Value types
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
    """Rolling per-window usage accumulator (ADR-047 §D2 per-window budget)."""

    used_tokens: int = 0
    used_cost: Decimal = Decimal("0")
    window_ref: str = "fx_exchange_agent:default"

    def add(self, cost: RequestCost) -> None:
        self.used_tokens += cost.tokens
        self.used_cost += cost.cost


@dataclass(frozen=True)
class FXExchangeMask:
    """Config-as-data FX/Exchange mask (ADR-049 §D3). Values are governed config,
    not hardcoded flow logic; the AUTO/REVIEW/BLOCK *scale* is ADR-047 canon."""

    cost_cap: CostCap
    auto_threshold: float = 0.90
    review_floor: float = 0.70
    autonomy_level: AutonomyLevel = AutonomyLevel.AUTO_BIASED
    lineage_obligation: bool = True
    # Money-movement step-up posture (ADR-049 §D4); read/quote never triggers it.
    require_step_up_for_money_movement: bool = True
    agent_id: str = "fx_exchange_agent"

    # The mask scope (ADR-049 §D3 allow-list): the only ports this mask may reach.
    # Increment 1 exposes the read/quote op only; the settlement + order ops are
    # DEFERRED to increment 2.
    scope: tuple[str, ...] = ("ExchangePort.get_rate",)

    # L3 compliance contour required before any exchange action (Ruflo mandatory).
    compliance_gate: tuple[str, ...] = ("AML", "SANCTIONS", "TRAVEL_RULE")


@dataclass
class RateQuoteIntent:
    """A resolved low-risk read intent (``ExchangePort.get_rate``) — the FX mask's
    "AUTO only for trivial, within-cap, low-risk reads" path (ADR-049 §D3/§D4)."""

    intent_text: str
    process_ref: ProcessRef
    base_asset: AssetSymbol
    quote_asset: AssetSymbol
    correlation_id: str
    confidence_score: float
    request_cost: RequestCost


@dataclass
class AgentDecisionRecord:
    """Decision-lineage record emitted per action (ADR-046 schema + ADR-047 cost)."""

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


@dataclass
class AgentOutcome:
    """Result of a masked action: the decision, whether a port was called, and the
    lineage record that was emitted (always non-None — lineage is non-optional)."""

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
# Internal evaluation
# ---------------------------------------------------------------------------


@dataclass
class _ActionContext:
    """All inputs a single masked action evaluates against."""

    intent_text: str
    process_ref: ProcessRef
    correlation_id: str
    confidence_score: float
    triggering_event: str
    success_action: str
    op: str
    request_cost: RequestCost
    compliance_result: ComplianceResult
    is_money_movement: bool
    amount: Decimal | None
    biometric_verified: bool
    human_reviewed_by: str | None


@dataclass
class _Evaluation:
    decision: ConfirmationDecision
    proceed: bool
    action_taken: str
    reasoning_summary: str
    policies: list[str]
    compliance_result: ComplianceResult
    budget_breach: BudgetBreach
    halt_reason: str | None = None
    requires_step_up: bool = False
    requires_hitl: bool = False


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class FXExchangeAgent:
    """L2 FX/Exchange agent enforcing the ADR-049 FX mask.

    Ports and the lineage recorder are injected as interfaces (constructor
    injection); the agent contains pure governance logic and is unit-testable
    without any live infra. ``wallet_port`` is injected for the increment-2
    settlement leg and is intentionally unused on the increment-1 read path.
    """

    def __init__(
        self,
        *,
        exchange_port: ExchangePort,
        wallet_port: WalletPort,
        recorder: DecisionRecorder,
        mask: FXExchangeMask,
        cost_window: CostWindow | None = None,
    ) -> None:
        self._exchange = exchange_port
        self._wallet = wallet_port  # TODO(increment 2): settlement leg via WalletPort.
        self._recorder = recorder
        self._mask = mask
        self._window = cost_window or CostWindow(window_ref=f"{mask.agent_id}:default")

    # -- public mask actions -------------------------------------------------

    async def get_rate(
        self,
        intent: RateQuoteIntent,
        *,
        compliance_result: ComplianceResult = ComplianceResult.PASS,
    ) -> AgentOutcome:
        """Low-risk read via ``ExchangePort.get_rate`` — AUTO-eligible, no step-up
        (the mask's trivial within-cap read path, ADR-049 §D3)."""
        ctx = _ActionContext(
            intent_text=intent.intent_text,
            process_ref=intent.process_ref,
            correlation_id=intent.correlation_id,
            confidence_score=intent.confidence_score,
            triggering_event=f"rate_quote:{intent.base_asset}/{intent.quote_asset}",
            success_action="QUOTE_RATE",
            op="ExchangePort.get_rate",
            request_cost=intent.request_cost,
            compliance_result=compliance_result,
            is_money_movement=False,
            amount=None,
            biometric_verified=False,
            human_reviewed_by=None,
        )
        return await self._run_action(
            ctx, lambda: self._exchange.get_rate(intent.base_asset, intent.quote_asset)
        )

    # TODO(increment 2): place_order — money movement, REVIEW-biased band, biometric
    #   step-up execution, idempotent on client_order_id (ExchangePort §place_order).
    # TODO(increment 2): cancel_order — idempotent cancellation (ExchangePort §cancel_order).
    # TODO(increment 2): the REVIEW flow (HITL hold/approve) for below-AUTO bands.
    # TODO(increment 2): WalletPort settlement leg for filled orders.

    # -- governance engine ---------------------------------------------------

    def _band(self, confidence: float) -> ConfirmationDecision:
        if confidence > self._mask.auto_threshold:
            return ConfirmationDecision.AUTO
        if confidence >= self._mask.review_floor:
            return ConfirmationDecision.REVIEW
        return ConfirmationDecision.BLOCK

    def _cost_breaches(self, cost: RequestCost) -> bool:
        cap = self._mask.cost_cap
        return (
            cost.tokens > cap.max_request_tokens
            or cost.cost > cap.max_request_cost
            or self._window.used_tokens + cost.tokens > cap.max_window_tokens
            or self._window.used_cost + cost.cost > cap.max_window_cost
        )

    def _step_up_required(self, ctx: _ActionContext) -> bool:
        # Increment 1 reads are never money movement, so this is always False here.
        # Increment 2 money movement is step-up-mandatory regardless of confidence.
        return ctx.is_money_movement and self._mask.require_step_up_for_money_movement

    def _evaluate(self, ctx: _ActionContext) -> _Evaluation:
        if not 0.0 <= ctx.confidence_score <= 1.0:
            raise ValueError("confidence_score must be in [0.0, 1.0]")
        policies = ["ADR-048-process-resolution"]

        # 1. ADR-048 — no port call without a resolved process_ref.
        if not ctx.process_ref.resolved:
            return _Evaluation(
                ConfirmationDecision.BLOCK,
                False,
                "HALT_UNRESOLVED_PROCESS",
                "Intent has no resolved process_ref; governance event, never improvised.",
                policies,
                ComplianceResult.NA,
                BudgetBreach.NONE,
                halt_reason="unresolved_process_ref",
                requires_hitl=True,
            )

        # 2. ADR-049 §D3 — mask scope allow-list; an off-list op is refused outright.
        policies.append("ADR-049-scope-allow-list")
        if ctx.op not in self._mask.scope:
            return _Evaluation(
                ConfirmationDecision.BLOCK,
                False,
                "REJECT_OUT_OF_SCOPE",
                f"Operation {ctx.op} is not on the FX mask scope allow-list; refused.",
                policies,
                ComplianceResult.NA,
                BudgetBreach.NONE,
                halt_reason="out_of_scope",
            )

        # 3. ADR-047 confidence band (AUTO > 0.90 / REVIEW 0.70–0.90 / BLOCK < 0.70).
        #    Increment 1 executes only the AUTO band; REVIEW is deferred (increment 2).
        policies.append("ADR-047-HITL-AUTO-REVIEW-BLOCK")
        band = self._band(ctx.confidence_score)
        if band is ConfirmationDecision.BLOCK:
            return _Evaluation(
                band,
                False,
                "BLOCK_LOW_CONFIDENCE",
                "Confidence < 0.70: full stop, human confirmation mandatory (ADR-049 §D4).",
                policies,
                ctx.compliance_result,
                BudgetBreach.NONE,
                halt_reason="low_confidence",
                requires_hitl=True,
            )
        if band is ConfirmationDecision.REVIEW:
            return _Evaluation(
                band,
                False,
                "HALT_REVIEW_DEFERRED",
                "Confidence in REVIEW band; REVIEW flow is deferred to increment 2 (ADR-049 §D4).",
                policies,
                ctx.compliance_result,
                BudgetBreach.NONE,
                halt_reason="review_deferred",
                requires_hitl=True,
            )

        # 4. ADR-047 — hard cost cap (per-request AND per-window).
        policies.append("ADR-047-cost-cap")
        if self._cost_breaches(ctx.request_cost):
            return _Evaluation(
                ConfirmationDecision.BLOCK,
                False,
                "HALT_COST_CAP_BREACH",
                "Cost-cap breach (per-request or per-window); action refused (ADR-047).",
                policies,
                ComplianceResult.NA,
                BudgetBreach.BREACH,
                halt_reason="cost_cap_breach",
            )

        # 5. L3 compliance gate (AML + sanctions + Travel Rule; Ruflo mandatory).
        policies.append("ADR-049-compliance-gate:" + "+".join(self._mask.compliance_gate))
        if ctx.compliance_result not in (ComplianceResult.PASS, ComplianceResult.NA):
            return _Evaluation(
                ConfirmationDecision.BLOCK,
                False,
                "HALT_COMPLIANCE_BLOCK",
                f"L3 compliance gate returned {ctx.compliance_result}; action blocked.",
                policies,
                ctx.compliance_result,
                BudgetBreach.NONE,
                halt_reason="compliance_block",
                requires_hitl=True,
            )

        # 6. ADR-049 §D4 — biometric step-up for critical money movement. The read/
        #    quote path is never money movement, so this never trips in increment 1.
        if self._step_up_required(ctx) and not ctx.biometric_verified:
            policies.append("ADR-049-D4-biometric-step-up")
            return _Evaluation(
                band,
                False,
                "HALT_STEP_UP_REQUIRED",
                "Critical money movement requires biometric step-up before commit (ADR-049 §D4).",
                policies,
                ctx.compliance_result,
                BudgetBreach.NONE,
                halt_reason="step_up_required",
                requires_step_up=True,
            )

        # All gates satisfied — clear to call the port.
        return _Evaluation(
            band,
            True,
            ctx.success_action,
            f"All mask gates satisfied at {band.value} confidence; executing within scope.",
            policies,
            ctx.compliance_result,
            BudgetBreach.NONE,
        )

    async def _run_action(
        self, ctx: _ActionContext, port_call: Callable[[], Awaitable[object]]
    ) -> AgentOutcome:
        ev = self._evaluate(ctx)
        result: object | None = None
        executed = False
        action_taken = ev.action_taken
        compliance_result = ev.compliance_result

        if ev.proceed:
            try:
                result = await port_call()
            except ExchangePortError as exc:
                action_taken = f"HALT_EXCHANGE_ERROR:{type(exc).__name__}"
                if isinstance(exc, ComplianceBlock):
                    compliance_result = ComplianceResult.FAIL
                await self._emit(
                    ctx,
                    ev,
                    action_taken,
                    executed=False,
                    compliance_result=compliance_result,
                    reasoning=f"Port rejected the action: {exc}",
                )
                raise
            executed = True
            self._window.add(ctx.request_cost)

        record = await self._emit(
            ctx,
            ev,
            action_taken,
            executed=executed,
            compliance_result=compliance_result,
            reasoning=ev.reasoning_summary,
        )
        return AgentOutcome(
            decision=ev.decision,
            executed=executed,
            record=record,
            result=result,
            halt_reason=ev.halt_reason,
            requires_step_up=ev.requires_step_up,
            requires_hitl=ev.requires_hitl,
        )

    async def _emit(
        self,
        ctx: _ActionContext,
        ev: _Evaluation,
        action_taken: str,
        *,
        executed: bool,
        compliance_result: ComplianceResult,
        reasoning: str,
    ) -> AgentDecisionRecord:
        record = AgentDecisionRecord(
            record_id=str(uuid.uuid4()),
            timestamp=datetime.now(UTC),
            agent_id=self._mask.agent_id,
            triggering_event=ctx.triggering_event,
            intent=ctx.intent_text,
            policies_evaluated=ev.policies,
            compliance_result=compliance_result,
            reasoning_summary=reasoning,
            confidence_score=ctx.confidence_score,
            action_taken=action_taken,
            human_reviewed_by=ctx.human_reviewed_by,
            correlation_id=ctx.correlation_id,
            cost_tokens=ctx.request_cost.tokens,
            cost_amount=ctx.request_cost.cost,
            budget_window_ref=self._window.window_ref,
            budget_breach_flag=ev.budget_breach,
        )
        await self._recorder.record(record)
        return record


__all__ = [
    "AgentDecisionRecord",
    "AgentOutcome",
    "AutonomyLevel",
    "BudgetBreach",
    "ComplianceResult",
    "ConfirmationDecision",
    "CostCap",
    "CostWindow",
    "DecisionRecorder",
    "FXExchangeAgent",
    "FXExchangeMask",
    "ProcessRef",
    "RateQuoteIntent",
    "RequestCost",
]
