"""FXExchangeAgent — L2 client-facing FX/Exchange agent (ADR-049 FX mask).

WHY: ADR-049 (Intent Layer & Client-Facing Agent Masks) specifies the
client-facing **FX/Exchange mask**: the governed surface through which a
resolved client intent becomes a bounded exchange action. This module mirrors
the Payments mask (``src/agents/payments_agent.py``) for the trading contour.
It implements the agent *logic* and *governance enforcement* of the FX mask; it
does NOT implement the ports, the LLM-orchestration/routing layer
(``AGENT_ROUTING_ENABLED`` stays out of scope — Terminal A infra, ADR-049
§D6/§D7), or the ClickHouse sink.

Two contours share one governance engine: the AUTO read/quote path
(``ExchangePort.get_rate``, increment 1) and the money-movement path
(``ExchangePort.place_order`` / ``ExchangePort.cancel_order`` with the
``WalletPort`` settlement leg, increment 2). Both traverse the identical §D2
gate chain; money movement is REVIEW-biased and adds biometric step-up.

The FX mask (ADR-049 §D3) enforced here, in §D2 chain order:

* ``scope``                — ExchangePort + WalletPort operations on an allow-list;
                             both ports are injected, never implemented here. An op
                             not on the allow-list is rejected.
* ``autonomy_level``       — AUTO-biased for the read/quote path; money movement is
                             REVIEW-biased and step-up-gated (ADR-049 §D3/§D4).
* ``confirmation_policy``  — AUTO > 0.90 / REVIEW 0.70–0.90 / BLOCK < 0.70
                             (ADR-047 thresholds, ADR-049 §D4). A read below AUTO
                             halts (re-quote); a money movement in the REVIEW band
                             holds for HITL and proceeds only once a human reviewer
                             is supplied (mirrors PaymentsAgent).
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
from functools import partial

from src.exchangeport.exchange_port import (
    AssetSymbol,
    ComplianceBlock,
    ExchangePort,
    ExchangePortError,
    OrderRequest,
    OrderResult,
    OrderState,
)
from src.wallet.wallet_port import ChainId, SeedMaterial, SignedTx, WalletPort

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
    movement is REVIEW-biased."""

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
    # The read/quote op, the two order ops, and the WalletPort settlement leg used
    # to deliver a filled order's asset to custody.
    scope: tuple[str, ...] = (
        "ExchangePort.get_rate",
        "ExchangePort.place_order",
        "ExchangePort.cancel_order",
        "WalletPort.sign_tx",
    )

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


@dataclass(frozen=True)
class SettlementInstruction:
    """Inputs for the WalletPort settlement leg of a FILLED order (ADR-021).

    The settlement leg signs the on-chain transfer that delivers a filled order's
    bought asset to the client custody address. ``seed`` is handed straight to the
    injected WalletPort custody adapter (the custody boundary) and is NEVER logged
    nor written to the lineage record (R-SEC-NEW-01 / ADR-046)."""

    chain: ChainId
    seed: SeedMaterial
    derivation_path: str
    tx_payload: object


@dataclass
class OrderIntent:
    """A resolved money-movement intent: place an exchange order under the FX mask.

    Carries the ADR-048 ``process_ref``, the idempotent ``OrderRequest`` (keyed on
    ``client_order_id``), the confidence the confirmation_policy bands on, the
    biometric step-up flag, and the optional WalletPort settlement instruction
    applied when the order fills (ADR-049 §D4)."""

    intent_text: str
    process_ref: ProcessRef
    order: OrderRequest
    correlation_id: str
    confidence_score: float
    request_cost: RequestCost
    biometric_verified: bool = False
    settlement: SettlementInstruction | None = None


@dataclass
class CancelOrderIntent:
    """A resolved intent to cancel an open exchange order (ExchangePort.cancel_order).

    Cancellation reduces exposure rather than moving client funds, so it is not a
    critical money movement (no biometric step-up); it still traverses the full §D2
    gate chain and is HITL-eligible in the REVIEW band (ADR-049 §D4)."""

    intent_text: str
    process_ref: ProcessRef
    order_id: str
    correlation_id: str
    confidence_score: float
    request_cost: RequestCost
    biometric_verified: bool = False


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
    # Money-movement actions hold for HITL in the REVIEW band; reads (AUTO-biased)
    # instead halt below AUTO. Defaults False so the read/quote path is unchanged.
    supports_review_hitl: bool = False


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
    without any live infra. ``wallet_port`` drives the settlement leg of a filled
    order and is held only as the injected interface, never implemented here.
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
        self._wallet = wallet_port
        self._recorder = recorder
        self._mask = mask
        self._window = cost_window or CostWindow(window_ref=f"{mask.agent_id}:default")
        # Agent-level idempotency on client_order_id: a replay returns the recorded
        # outcome without re-placing or re-emitting (ExchangePort.place_order is also
        # idempotent at the adapter layer — ADR-021). Only executed orders are cached.
        self._placed: dict[str, AgentOutcome] = {}

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

    async def place_order(
        self,
        intent: OrderIntent,
        *,
        compliance_result: ComplianceResult = ComplianceResult.PASS,
        human_reviewed_by: str | None = None,
    ) -> AgentOutcome:
        """Place an exchange order — critical money movement, REVIEW-biased band.

        Enforces the full §D2 gate chain; requires biometric step-up regardless of
        confidence band (ADR-049 §D4). Idempotent on ``order.client_order_id``: a
        replay of an already-placed order returns the recorded outcome without
        re-calling the port. A FILLED order triggers the WalletPort settlement leg.
        """
        cached = self._placed.get(intent.order.client_order_id)
        if cached is not None:
            return cached
        ctx = _ActionContext(
            intent_text=intent.intent_text,
            process_ref=intent.process_ref,
            correlation_id=intent.correlation_id,
            confidence_score=intent.confidence_score,
            triggering_event=f"order_intent:{intent.order.client_order_id}",
            success_action="PLACE_ORDER",
            op="ExchangePort.place_order",
            request_cost=intent.request_cost,
            compliance_result=compliance_result,
            is_money_movement=True,
            amount=Decimal(intent.order.amount),
            biometric_verified=intent.biometric_verified,
            human_reviewed_by=human_reviewed_by,
            supports_review_hitl=True,
        )
        settle = partial(self._settle, intent.settlement) if intent.settlement else None
        outcome = await self._run_action(
            ctx, lambda: self._exchange.place_order(intent.order), settle=settle
        )
        if outcome.executed:
            self._placed[intent.order.client_order_id] = outcome
        return outcome

    async def cancel_order(
        self,
        intent: CancelOrderIntent,
        *,
        compliance_result: ComplianceResult = ComplianceResult.PASS,
        human_reviewed_by: str | None = None,
    ) -> AgentOutcome:
        """Cancel an open exchange order via ``ExchangePort.cancel_order``.

        Idempotent at the port: cancelling an order already in a final state returns
        ``False`` without raising. Traverses the full §D2 gate chain and is
        HITL-eligible in the REVIEW band; not a critical money movement, so no
        biometric step-up (ADR-049 §D4)."""
        ctx = _ActionContext(
            intent_text=intent.intent_text,
            process_ref=intent.process_ref,
            correlation_id=intent.correlation_id,
            confidence_score=intent.confidence_score,
            triggering_event=f"cancel_order:{intent.order_id}",
            success_action="CANCEL_ORDER",
            op="ExchangePort.cancel_order",
            request_cost=intent.request_cost,
            compliance_result=compliance_result,
            is_money_movement=False,
            amount=None,
            biometric_verified=intent.biometric_verified,
            human_reviewed_by=human_reviewed_by,
            supports_review_hitl=True,
        )
        return await self._run_action(ctx, lambda: self._exchange.cancel_order(intent.order_id))

    async def _settle(self, settlement: SettlementInstruction, result: OrderResult) -> SignedTx:
        """WalletPort settlement leg for a FILLED order (ADR-021): sign the on-chain
        transfer that delivers the bought asset to custody. ``seed`` goes only to the
        injected custody adapter and is never logged nor recorded (R-SEC-NEW-01)."""
        return await self._wallet.sign_tx(
            settlement.seed,
            settlement.chain,
            settlement.derivation_path,
            settlement.tx_payload,
        )

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
            # Read/quote path: reads are AUTO-only under this AUTO-biased mask, so a
            # below-AUTO read halts (re-quote at higher confidence), not a HITL hold.
            if not ctx.supports_review_hitl:
                return _Evaluation(
                    band,
                    False,
                    "HALT_REVIEW_DEFERRED",
                    "Read intent below AUTO band; reads are AUTO-only, no HITL hold (ADR-049 §D3).",
                    policies,
                    ctx.compliance_result,
                    BudgetBreach.NONE,
                    halt_reason="review_deferred",
                    requires_hitl=True,
                )
            # Money-movement path: REVIEW band holds for HITL and proceeds only once a
            # human reviewer is supplied (mirrors PaymentsAgent, ADR-049 §D4).
            if ctx.human_reviewed_by is None:
                return _Evaluation(
                    band,
                    False,
                    "HOLD_FOR_REVIEW",
                    "Confidence in REVIEW band: paused for HITL; escalates to BLOCK on no response.",
                    policies,
                    ctx.compliance_result,
                    BudgetBreach.NONE,
                    halt_reason="hitl_review_required",
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

        # 6. ADR-049 §D4 — biometric step-up for critical money movement, mandatory
        #    regardless of confidence band. The read/quote path is never money
        #    movement, so this gate trips only for place_order.
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
        reviewer = (
            "" if ctx.human_reviewed_by is None else f" (reviewed by {ctx.human_reviewed_by})"
        )
        return _Evaluation(
            band,
            True,
            ctx.success_action,
            f"All mask gates satisfied at {band.value} confidence{reviewer}; executing within scope.",
            policies,
            ctx.compliance_result,
            BudgetBreach.NONE,
        )

    async def _run_action(
        self,
        ctx: _ActionContext,
        port_call: Callable[[], Awaitable[object]],
        *,
        settle: Callable[[OrderResult], Awaitable[SignedTx]] | None = None,
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
            # WalletPort settlement leg (ADR-021): only a FILLED order settles. The
            # whole place_order action still owes exactly one lineage record, so a
            # settlement failure is recorded here and re-raised (never a silent gap).
            if (
                settle is not None
                and isinstance(result, OrderResult)
                and result.state is OrderState.FILLED
            ):
                try:
                    await settle(result)
                except Exception as exc:  # noqa: BLE001 — re-raised after recording lineage
                    action_taken = f"HALT_SETTLEMENT_ERROR:{type(exc).__name__}"
                    await self._emit(
                        ctx,
                        ev,
                        action_taken,
                        executed=True,
                        compliance_result=compliance_result,
                        reasoning=f"Order placed but WalletPort settlement leg failed: {exc}",
                    )
                    raise

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
    "CancelOrderIntent",
    "ComplianceResult",
    "ConfirmationDecision",
    "CostCap",
    "CostWindow",
    "DecisionRecorder",
    "FXExchangeAgent",
    "FXExchangeMask",
    "OrderIntent",
    "ProcessRef",
    "RateQuoteIntent",
    "RequestCost",
    "SettlementInstruction",
]
