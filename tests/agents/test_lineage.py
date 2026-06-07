"""Unit tests for the canonical shared lineage/cost primitives (``_lineage``).

These cover the primitives in isolation (the per-agent behaviour is covered by
``test_payments_agent``/``test_fx_exchange_agent``/``test_wallet_agent``, which
exercise the same imported classes after the DRY extraction):

* ``CostCap`` breach reasoning per-request AND per-window (ADR-047 §D2).
* ``CostWindow`` accumulation and the generic default ``window_ref``.
* ``ProcessRef.resolved`` truth table (ADR-048).
* ``AgentDecisionRecord`` ADR-046 §D5 additive fields default to ``None`` and
  the optional input/output token split refines the existing ``cost_tokens``.
* ``DecisionRecorder`` is the injectable ABC seam.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.agents._lineage import (
    AgentDecisionRecord,
    AgentOutcome,
    BudgetBreach,
    ComplianceResult,
    ConfirmationDecision,
    CostCap,
    CostWindow,
    DecisionRecorder,
    ProcessRef,
    RequestCost,
)

# ── ProcessRef (ADR-048) ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("process_id", "version", "resolved"),
    [
        ("P-1", "v1", True),
        ("", "v1", False),
        ("P-1", "", False),
        ("", "", False),
    ],
)
def test_process_ref_resolved(process_id: str, version: str, resolved: bool) -> None:
    assert ProcessRef(process_id=process_id, version=version).resolved is resolved


# ── CostWindow (ADR-047 §D2) ──────────────────────────────────────────────────


def test_cost_window_default_window_ref_is_generic() -> None:
    # The generic default; each agent overrides it with f"{agent_id}:default".
    assert CostWindow().window_ref == "agent:default"
    assert CostWindow().used_tokens == 0
    assert CostWindow().used_cost == Decimal("0")


def test_cost_window_add_accumulates_both_dimensions() -> None:
    window = CostWindow()
    window.add(RequestCost(tokens=100, cost=Decimal("0.50")))
    window.add(RequestCost(tokens=25, cost=Decimal("0.25")))
    assert window.used_tokens == 125
    assert window.used_cost == Decimal("0.75")


# ── CostCap breach reasoning (per-request AND per-window) ──────────────────────


def _cap() -> CostCap:
    return CostCap(
        max_request_tokens=1_000,
        max_request_cost=Decimal("1.00"),
        max_window_tokens=10_000,
        max_window_cost=Decimal("10.00"),
    )


def _breaches(cap: CostCap, window: CostWindow, cost: RequestCost) -> bool:
    """Mirror of each agent's ``_cost_breaches`` predicate (ADR-047 §D2)."""
    return (
        cost.tokens > cap.max_request_tokens
        or cost.cost > cap.max_request_cost
        or window.used_tokens + cost.tokens > cap.max_window_tokens
        or window.used_cost + cost.cost > cap.max_window_cost
    )


def test_cost_cap_within_all_dimensions_does_not_breach() -> None:
    assert _breaches(_cap(), CostWindow(), RequestCost(tokens=500, cost=Decimal("0.50"))) is False


def test_cost_cap_per_request_token_breach() -> None:
    assert _breaches(_cap(), CostWindow(), RequestCost(tokens=1_001, cost=Decimal("0.01"))) is True


def test_cost_cap_per_request_cost_breach() -> None:
    assert _breaches(_cap(), CostWindow(), RequestCost(tokens=1, cost=Decimal("1.01"))) is True


def test_cost_cap_per_window_token_breach() -> None:
    window = CostWindow(used_tokens=9_900, used_cost=Decimal("0.00"))
    assert _breaches(_cap(), window, RequestCost(tokens=200, cost=Decimal("0.01"))) is True


def test_cost_cap_per_window_cost_breach() -> None:
    window = CostWindow(used_tokens=0, used_cost=Decimal("9.99"))
    assert _breaches(_cap(), window, RequestCost(tokens=1, cost=Decimal("0.50"))) is True


# ── AgentDecisionRecord (ADR-046 + §D5 additive fields) ───────────────────────


def _record(**overrides: object) -> AgentDecisionRecord:
    base: dict[str, object] = dict(
        record_id="r-1",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        agent_id="test_agent",
        triggering_event="evt",
        intent="do a thing",
        policies_evaluated=["ADR-048-process-resolution"],
        compliance_result=ComplianceResult.PASS,
        reasoning_summary="ok",
        confidence_score=0.95,
        action_taken="DONE",
        human_reviewed_by=None,
        correlation_id="c-1",
    )
    base.update(overrides)
    return AgentDecisionRecord(**base)  # type: ignore[arg-type]


def test_decision_record_cost_defaults() -> None:
    rec = _record()
    assert rec.cost_tokens == 0
    assert rec.cost_amount == Decimal("0")
    assert rec.budget_window_ref == ""
    assert rec.budget_breach_flag is BudgetBreach.NONE


def test_decision_record_d5_fields_default_none() -> None:
    # ADR-046 §D5 additive fields are non-breaking and default to None.
    rec = _record()
    assert rec.immutable_storage_ref is None
    assert rec.input_tokens is None
    assert rec.output_tokens is None


def test_decision_record_d5_token_split_is_optional_and_refines_total() -> None:
    # The input/output split is optional; when supplied it refines the existing
    # cost_tokens TOTAL (which stays authoritative), not replaces it.
    rec = _record(cost_tokens=300, input_tokens=200, output_tokens=100)
    assert rec.cost_tokens == 300
    assert rec.input_tokens + rec.output_tokens == rec.cost_tokens


def test_decision_record_d5_immutable_storage_ref_settable() -> None:
    rec = _record(immutable_storage_ref="worm://lineage/r-1")
    assert rec.immutable_storage_ref == "worm://lineage/r-1"


# ── AgentOutcome + DecisionRecorder seam ──────────────────────────────────────


def test_agent_outcome_defaults() -> None:
    rec = _record()
    outcome = AgentOutcome(decision=ConfirmationDecision.AUTO, executed=True, record=rec)
    assert outcome.result is None
    assert outcome.halt_reason is None
    assert outcome.requires_step_up is False
    assert outcome.requires_hitl is False


async def test_decision_recorder_is_injectable_abc() -> None:
    captured: list[AgentDecisionRecord] = []

    class _Recorder(DecisionRecorder):
        async def record(self, record: AgentDecisionRecord) -> None:
            captured.append(record)

    rec = _record()
    await _Recorder().record(rec)
    assert captured == [rec]


def test_decision_recorder_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        DecisionRecorder()  # type: ignore[abstract]
