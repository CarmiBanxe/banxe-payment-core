"""PaymentsAgent — L2 client-facing Payments agent (ADR-049 Payments mask).

WHY: ADR-049 (Intent Layer & Client-Facing Agent Masks) specifies the
client-facing **Payments mask**: the governed surface through which a resolved
client intent becomes a bounded payment action. This module is the first
implementation of the Intent-First **L2 Execution** layer. It implements the
agent *logic* and *governance enforcement* of the Payments mask; it does NOT
implement the ports, the LLM-orchestration/routing layer (``AGENT_ROUTING_ENABLED``
stays out of scope — Terminal A infra, ADR-049 §D6/§D7), or the ClickHouse sink.

The Payments mask (ADR-049 §D3) enforced here:

* ``scope``                — WalletPort + PartnerPort operations only (the allow-list);
                             both ports are injected, never implemented here.
* ``autonomy_level``       — REVIEW-biased for money movement (ADR-049 §D3).
* ``confirmation_policy``  — AUTO > 0.90 / REVIEW 0.70–0.90 / BLOCK < 0.70
                             (ADR-047 thresholds, ADR-049 §D4) + biometric step-up
                             for critical money movement, independent of confidence.
* ``cost_cap``             — per-request AND per-window hard caps, token AND
                             monetary (Decimal) dimensions (ADR-047 §D2, ADR-049 §D3).
* ``lineage_obligation``   — one ``AgentDecisionRecord`` per action (ADR-046), non-optional.
* ``compliance_gate``      — AML + sanctions + Travel-Rule contour (Ruflo mandatory,
                             L3); a non-PASS result halts (ADR-049 §D3/§D4).

Any one of {unresolved process_ref, cost-cap breach, compliance fail, low
confidence, missing step-up} halts the action (ADR-049 §D4 — independent halt
conditions). Mask *values* (caps, materiality, thresholds) are config-as-data
(CLAUDE.md §10), carried on :class:`PaymentsMask`, never hardcoded in flow logic.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from src.agents._lineage import (
    AgentDecisionRecord,
    AgentOutcome,
    BudgetBreach,
    ComplianceResult,
    ConfirmationDecision,
    CostCap,
    CostWindow,
    DecisionRecorder,
    IdempotencyStorePort,
    InMemoryIdempotencyStore,
    ProcessRef,
    RequestCost,
)
from src.partner.partner_port import (
    ComplianceBlock,
    PartnerPort,
    PartnerPortError,
    PaymentInstruction,
)
from src.wallet.wallet_port import ChainId, WalletPort

# ---------------------------------------------------------------------------
# Mask vocabulary — shared lineage/cost primitives (ConfirmationDecision,
# ComplianceResult, BudgetBreach, ProcessRef, RequestCost, CostCap, CostWindow,
# AgentDecisionRecord, AgentOutcome, DecisionRecorder) now live in
# ``src/agents/_lineage.py`` and are imported above. Only Payments-mask-specific
# types are defined below.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PaymentsMask:
    """Config-as-data Payments mask (ADR-049 §D3). Values are governed config,
    not hardcoded flow logic; the AUTO/REVIEW/BLOCK *scale* is ADR-047 canon."""

    cost_cap: CostCap
    step_up_materiality: Decimal
    auto_threshold: float = 0.90
    review_floor: float = 0.70
    require_step_up_for_money_movement: bool = True
    agent_id: str = "payments_agent"

    # The mask scope (ADR-049 §D3 allow-list): the only ports this mask may reach.
    scope: tuple[str, ...] = (
        "PartnerPort.initiate_payment",
        "WalletPort.validate_address",
    )


@dataclass
class PaymentIntent:
    """A resolved client intent handed across the L1→L2 boundary (ADR-049 §D2).

    Carries the ADR-048 ``process_ref``, the structured payment instruction, the
    confidence score the confirmation_policy bands on, and the biometric step-up flag.
    """

    intent_text: str
    process_ref: ProcessRef
    instruction: PaymentInstruction
    correlation_id: str
    confidence_score: float
    request_cost: RequestCost
    biometric_verified: bool = False


@dataclass
class AddressValidationIntent:
    """A resolved low-risk read intent (WalletPort.validate_address) — the mask's
    "AUTO only for trivial, within-cap, low-risk reads" path (ADR-049 §D3/§D4)."""

    intent_text: str
    process_ref: ProcessRef
    chain: ChainId
    address: str
    correlation_id: str
    confidence_score: float
    request_cost: RequestCost


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


class PaymentsAgent:
    """L2 Payments agent enforcing the ADR-049 Payments mask.

    Ports and the lineage recorder are injected as interfaces (constructor
    injection); the agent contains pure governance logic and is unit-testable
    without any live infra.
    """

    def __init__(
        self,
        *,
        wallet_port: WalletPort,
        partner_port: PartnerPort,
        recorder: DecisionRecorder,
        mask: PaymentsMask,
        cost_window: CostWindow | None = None,
        idempotency_store: IdempotencyStorePort | None = None,
    ) -> None:
        self._wallet = wallet_port
        self._partner = partner_port
        self._recorder = recorder
        self._mask = mask
        self._window = cost_window or CostWindow(window_ref=f"{mask.agent_id}:default")
        # Idempotent replay protection for money movement (S5.3): a retried or
        # double-submitted intent carrying an already-executed idempotency_key is
        # suppressed, never paid twice. Injected; defaults to an in-process store.
        self._idempotency = idempotency_store or InMemoryIdempotencyStore()

    # -- public mask actions -------------------------------------------------

    async def initiate_payment(
        self,
        intent: PaymentIntent,
        *,
        compliance_result: ComplianceResult = ComplianceResult.PASS,
        human_reviewed_by: str | None = None,
    ) -> AgentOutcome:
        """Move client funds via ``PartnerPort.initiate_payment`` under the mask.

        Critical money movement at/above the mask materiality requires biometric
        step-up regardless of confidence band (ADR-049 §D4).
        """
        amount = Decimal(intent.instruction.amount)
        key = intent.instruction.idempotency_key
        ctx = _ActionContext(
            intent_text=intent.intent_text,
            process_ref=intent.process_ref,
            correlation_id=intent.correlation_id,
            confidence_score=intent.confidence_score,
            triggering_event=f"payment_intent:{key}",
            success_action="APPROVE_PAYMENT",
            request_cost=intent.request_cost,
            compliance_result=compliance_result,
            is_money_movement=True,
            amount=amount,
            biometric_verified=intent.biometric_verified,
            human_reviewed_by=human_reviewed_by,
        )
        # Idempotent replay protection (S5.3): an idempotency_key already executed
        # to completion is suppressed — returned with the prior result, never paid
        # again. Checked before dispatch so no second money movement is attempted.
        if await self._idempotency.has_seen(key):
            return await self._suppress_duplicate(ctx, key)
        outcome = await self._run_action(
            ctx, lambda: self._partner.initiate_payment(intent.instruction)
        )
        # Mark the key only after a successful execution: a halted/failed payment
        # never marks, so a legitimate retry of an unfinished payment still runs.
        if outcome.executed:
            await self._idempotency.mark_seen(key, outcome.result)
        return outcome

    async def validate_address(
        self,
        intent: AddressValidationIntent,
        *,
        compliance_result: ComplianceResult = ComplianceResult.PASS,
    ) -> AgentOutcome:
        """Low-risk read via ``WalletPort.validate_address`` — AUTO-eligible, no
        step-up (the mask's trivial within-cap read path, ADR-049 §D3)."""
        ctx = _ActionContext(
            intent_text=intent.intent_text,
            process_ref=intent.process_ref,
            correlation_id=intent.correlation_id,
            confidence_score=intent.confidence_score,
            triggering_event=f"address_check:{intent.chain.value}:{intent.address}",
            success_action="VALIDATE_ADDRESS",
            request_cost=intent.request_cost,
            compliance_result=compliance_result,
            is_money_movement=False,
            amount=None,
            biometric_verified=False,
            human_reviewed_by=None,
        )
        return await self._run_action(
            ctx, lambda: self._wallet.validate_address(intent.chain, intent.address)
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
        if not (ctx.is_money_movement and self._mask.require_step_up_for_money_movement):
            return False
        if ctx.amount is None:
            return True
        return ctx.amount >= self._mask.step_up_materiality

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

        # 2. ADR-047 — hard cost cap (per-request AND per-window).
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

        # 3. L3 compliance gate (AML + sanctions + Travel Rule; Ruflo mandatory).
        policies.append("ADR-049-compliance-gate:AML+SANCTIONS+TRAVEL_RULE")
        if ctx.compliance_result not in (ComplianceResult.PASS, ComplianceResult.NA):
            return _Evaluation(
                ConfirmationDecision.BLOCK,
                False,
                "HALT_COMPLIANCE_BLOCK",
                f"L3 compliance gate returned {ctx.compliance_result}; payment blocked.",
                policies,
                ctx.compliance_result,
                BudgetBreach.NONE,
                halt_reason="compliance_block",
                requires_hitl=True,
            )

        # 4. ADR-047 confidence band (AUTO > 0.90 / REVIEW 0.70–0.90 / BLOCK < 0.70).
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
        if band is ConfirmationDecision.REVIEW and ctx.human_reviewed_by is None:
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

        # 5. ADR-049 §D4 — biometric step-up for critical money movement.
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

    async def _suppress_duplicate(self, ctx: _ActionContext, key: str) -> AgentOutcome:
        """Handle an idempotent replay: emit exactly one auditable
        ``DUPLICATE_SUPPRESSED`` lineage record and return the prior result without
        re-executing or moving funds (S5.3). No budget is consumed (the window is
        untouched) and no compliance gate is re-run — the original action already
        passed every gate when it executed."""
        prior = await self._idempotency.prior_result(key)
        ev = _Evaluation(
            decision=ConfirmationDecision.AUTO,
            proceed=False,
            action_taken="DUPLICATE_SUPPRESSED",
            reasoning_summary=(
                "Idempotent replay: idempotency_key already executed; re-execution "
                "suppressed (no double-payment), prior result returned (ADR-046)."
            ),
            policies=["ADR-046-idempotent-replay-protection"],
            compliance_result=ComplianceResult.NA,
            budget_breach=BudgetBreach.NONE,
            halt_reason="duplicate_suppressed",
        )
        record = await self._emit(
            ctx,
            ev,
            ev.action_taken,
            executed=False,
            compliance_result=ComplianceResult.NA,
            reasoning=ev.reasoning_summary,
        )
        return AgentOutcome(
            decision=ev.decision,
            executed=False,
            record=record,
            result=prior,
            halt_reason=ev.halt_reason,
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
            except PartnerPortError as exc:
                action_taken = f"HALT_PARTNER_ERROR:{type(exc).__name__}"
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
    "AddressValidationIntent",
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
    "PaymentIntent",
    "PaymentsAgent",
    "PaymentsMask",
    "ProcessRef",
    "RequestCost",
]
