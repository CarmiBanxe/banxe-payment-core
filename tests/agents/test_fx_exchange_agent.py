"""Tests for the ADR-049 FX/Exchange mask agent (src/agents/fx_exchange_agent.py).

Increment 1 covers the AUTO read/quote path (``ExchangePort.get_rate``): the
scope allow-list, the AUTO happy path, unresolved process_ref, cost-cap breach,
below-AUTO confidence halts, the compliance gate, and the lineage-per-action
obligation (ADR-046). Ports and the recorder are fakes — the agent is exercised
as pure governance logic with no live infra; no real adapters are imported.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.agents.fx_exchange_agent import (
    AgentDecisionRecord,
    BudgetBreach,
    ComplianceResult,
    ConfirmationDecision,
    CostCap,
    CostWindow,
    DecisionRecorder,
    FXExchangeAgent,
    FXExchangeMask,
    ProcessRef,
    RateQuoteIntent,
    RequestCost,
)
from src.exchangeport.exchange_port import (
    AssetSymbol,
    ComplianceBlock,
    ExchangePort,
    ExchangeUnavailable,
    OrderRequest,
    OrderResult,
    RateQuote,
)
from src.wallet.wallet_port import ChainId, WalletPort

# ── Fakes (the ports & sink are injected interfaces; we never implement them in src) ──


class FakeRecorder(DecisionRecorder):
    def __init__(self) -> None:
        self.records: list[AgentDecisionRecord] = []

    async def record(self, record: AgentDecisionRecord) -> None:
        self.records.append(record)


class FakeExchangePort(ExchangePort):
    def __init__(
        self, quote: RateQuote | None = None, raises: Exception | None = None
    ) -> None:
        self._quote = quote or RateQuote(
            base_asset="BTC",
            quote_asset="EUR",
            bid="60000.00",
            ask="60010.00",
            ttl_seconds=5,
            quoted_at="2026-06-07T00:00:00Z",
        )
        self._raises = raises
        self.calls: list[tuple[AssetSymbol, AssetSymbol]] = []

    async def get_rate(self, base: AssetSymbol, quote: AssetSymbol) -> RateQuote:
        self.calls.append((base, quote))
        if self._raises is not None:
            raise self._raises
        return self._quote

    async def place_order(self, order: OrderRequest) -> OrderResult:  # pragma: no cover
        raise NotImplementedError

    async def cancel_order(self, order_id: str) -> bool:  # pragma: no cover
        raise NotImplementedError

    async def get_order_status(self, order_id: str) -> OrderResult:  # pragma: no cover
        raise NotImplementedError


class FakeWalletPort(WalletPort):
    """Injected for the increment-2 settlement leg; unused on the read path."""

    async def validate_address(self, chain: ChainId, address: str) -> bool:  # pragma: no cover
        raise NotImplementedError

    async def generate_seed_phrase(self, word_count):  # pragma: no cover
        raise NotImplementedError

    async def seed_phrase_to_entropy(self, phrase):  # pragma: no cover
        raise NotImplementedError

    async def derive_address(self, seed, chain, path):  # pragma: no cover
        raise NotImplementedError

    async def sign_tx(self, seed, chain, path, tx_payload):  # pragma: no cover
        raise NotImplementedError

    async def verify_signature(self, chain, address, message, signature):  # pragma: no cover
        raise NotImplementedError

    async def encrypt(self, plaintext, password):  # pragma: no cover
        raise NotImplementedError

    async def decrypt(self, ciphertext, password):  # pragma: no cover
        raise NotImplementedError


# ── Builders ──────────────────────────────────────────────────────────────────


def make_mask(**overrides) -> FXExchangeMask:
    base = {
        "cost_cap": CostCap(
            max_request_tokens=10_000,
            max_request_cost=Decimal("1.00"),
            max_window_tokens=100_000,
            max_window_cost=Decimal("10.00"),
        ),
    }
    base.update(overrides)
    return FXExchangeMask(**base)


def make_intent(
    *,
    confidence: float = 0.95,
    cost: RequestCost | None = None,
    process_ref: ProcessRef | None = None,
) -> RateQuoteIntent:
    return RateQuoteIntent(
        intent_text="What is the BTC/EUR rate?",
        process_ref=process_ref or ProcessRef(process_id="PROC-FX-QUOTE", version="1"),
        base_asset="BTC",
        quote_asset="EUR",
        correlation_id="corr-fx-1",
        confidence_score=confidence,
        request_cost=cost or RequestCost(tokens=50, cost=Decimal("0.01")),
    )


def make_agent(
    *,
    mask: FXExchangeMask | None = None,
    exchange: FakeExchangePort | None = None,
    recorder: FakeRecorder | None = None,
    cost_window: CostWindow | None = None,
) -> tuple[FXExchangeAgent, FakeExchangePort, FakeRecorder]:
    exchange = exchange or FakeExchangePort()
    recorder = recorder or FakeRecorder()
    agent = FXExchangeAgent(
        exchange_port=exchange,
        wallet_port=FakeWalletPort(),
        recorder=recorder,
        mask=mask or make_mask(),
        cost_window=cost_window,
    )
    return agent, exchange, recorder


# ── Scope allow-list ────────────────────────────────────────────────────────


async def test_out_of_scope_op_rejected():
    # A mask whose allow-list omits get_rate must refuse the read outright.
    agent, exchange, recorder = make_agent(mask=make_mask(scope=()))
    outcome = await agent.get_rate(make_intent())

    assert outcome.decision is ConfirmationDecision.BLOCK
    assert outcome.executed is False
    assert outcome.halt_reason == "out_of_scope"
    assert exchange.calls == []
    assert recorder.records[0].action_taken == "REJECT_OUT_OF_SCOPE"


# ── AUTO quote happy path ─────────────────────────────────────────────────────


async def test_auto_quote_happy_path():
    agent, exchange, recorder = make_agent()
    outcome = await agent.get_rate(make_intent(confidence=0.95))

    assert outcome.decision is ConfirmationDecision.AUTO
    assert outcome.executed is True
    assert isinstance(outcome.result, RateQuote)
    assert outcome.requires_step_up is False
    assert exchange.calls == [("BTC", "EUR")]
    assert recorder.records[0].action_taken == "QUOTE_RATE"
    assert len(recorder.records) == 1


# ── Process resolution (ADR-048) ──────────────────────────────────────────────


async def test_unresolved_process_ref_halts():
    agent, exchange, recorder = make_agent()
    outcome = await agent.get_rate(make_intent(process_ref=ProcessRef(process_id="", version="")))

    assert outcome.decision is ConfirmationDecision.BLOCK
    assert outcome.halt_reason == "unresolved_process_ref"
    assert exchange.calls == []
    assert recorder.records[0].action_taken == "HALT_UNRESOLVED_PROCESS"


# ── Cost-cap breach (ADR-047) ─────────────────────────────────────────────────


async def test_per_request_cost_cap_breach_halts():
    agent, exchange, recorder = make_agent()
    outcome = await agent.get_rate(
        make_intent(cost=RequestCost(tokens=999_999, cost=Decimal("0.01")))
    )

    assert outcome.decision is ConfirmationDecision.BLOCK
    assert outcome.executed is False
    assert outcome.halt_reason == "cost_cap_breach"
    assert exchange.calls == []
    assert recorder.records[0].budget_breach_flag is BudgetBreach.BREACH
    assert recorder.records[0].action_taken == "HALT_COST_CAP_BREACH"


async def test_per_window_cost_cap_breach_halts():
    window = CostWindow(used_tokens=99_900, used_cost=Decimal("0.00"))
    agent, exchange, _ = make_agent(cost_window=window)
    outcome = await agent.get_rate(make_intent(cost=RequestCost(tokens=200, cost=Decimal("0.01"))))

    assert outcome.decision is ConfirmationDecision.BLOCK
    assert outcome.halt_reason == "cost_cap_breach"
    assert exchange.calls == []


async def test_window_accumulates_on_successful_quote():
    window = CostWindow()
    agent, _, _ = make_agent(cost_window=window)
    await agent.get_rate(make_intent(cost=RequestCost(tokens=40, cost=Decimal("0.02"))))
    assert window.used_tokens == 40
    assert window.used_cost == Decimal("0.02")


# ── Confidence band: increment 1 executes AUTO only ───────────────────────────


async def test_review_band_deferred_to_increment_2():
    agent, exchange, recorder = make_agent()
    outcome = await agent.get_rate(make_intent(confidence=0.80))

    assert outcome.decision is ConfirmationDecision.REVIEW
    assert outcome.executed is False
    assert outcome.halt_reason == "review_deferred"
    assert exchange.calls == []
    assert recorder.records[0].action_taken == "HALT_REVIEW_DEFERRED"


async def test_block_low_confidence_halts():
    agent, exchange, recorder = make_agent()
    outcome = await agent.get_rate(make_intent(confidence=0.50))

    assert outcome.decision is ConfirmationDecision.BLOCK
    assert outcome.executed is False
    assert outcome.halt_reason == "low_confidence"
    assert exchange.calls == []
    assert recorder.records[0].action_taken == "BLOCK_LOW_CONFIDENCE"


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [
        (0.91, ConfirmationDecision.AUTO),
        (0.90, ConfirmationDecision.REVIEW),
        (0.70, ConfirmationDecision.REVIEW),
        (0.6999, ConfirmationDecision.BLOCK),
    ],
)
async def test_confidence_band_boundaries(confidence, expected):
    agent, _, _ = make_agent()
    outcome = await agent.get_rate(make_intent(confidence=confidence))
    assert outcome.decision is expected


# ── Compliance gate (ADR-049 §D3/§D4) ─────────────────────────────────────────


async def test_compliance_fail_halts():
    agent, exchange, recorder = make_agent()
    outcome = await agent.get_rate(make_intent(), compliance_result=ComplianceResult.FAIL)

    assert outcome.decision is ConfirmationDecision.BLOCK
    assert outcome.executed is False
    assert exchange.calls == []
    assert recorder.records[0].compliance_result is ComplianceResult.FAIL
    assert recorder.records[0].action_taken == "HALT_COMPLIANCE_BLOCK"


async def test_exchange_compliance_block_records_then_raises():
    exchange = FakeExchangePort(raises=ComplianceBlock("sanctioned pair"))
    agent, exchange, recorder = make_agent(exchange=exchange)
    with pytest.raises(ComplianceBlock):
        await agent.get_rate(make_intent(confidence=0.99))
    # Lineage emitted even on port failure.
    assert len(recorder.records) == 1
    assert recorder.records[0].compliance_result is ComplianceResult.FAIL
    assert recorder.records[0].action_taken.startswith("HALT_EXCHANGE_ERROR:")


async def test_exchange_unavailable_records_then_raises():
    exchange = FakeExchangePort(raises=ExchangeUnavailable("upstream 503"))
    agent, exchange, recorder = make_agent(exchange=exchange)
    with pytest.raises(ExchangeUnavailable):
        await agent.get_rate(make_intent(confidence=0.99))
    assert len(recorder.records) == 1
    assert recorder.records[0].action_taken == "HALT_EXCHANGE_ERROR:ExchangeUnavailable"


# ── Lineage obligation (ADR-046) — exactly one record per action ──────────────


async def test_exactly_one_record_per_action():
    agent, _, recorder = make_agent()
    await agent.get_rate(make_intent())
    await agent.get_rate(make_intent(confidence=0.50))
    assert len(recorder.records) == 2  # one per action, including the halt

    rec = recorder.records[0]
    assert rec.record_id
    assert rec.timestamp.tzinfo is not None
    assert rec.agent_id == "fx_exchange_agent"
    assert rec.intent == "What is the BTC/EUR rate?"
    assert rec.correlation_id == "corr-fx-1"
    assert rec.policies_evaluated  # non-empty ordered policy list
    assert 0.0 <= rec.confidence_score <= 1.0
    assert rec.cost_tokens == 50
    assert rec.cost_amount == Decimal("0.01")
    assert rec.budget_window_ref == "fx_exchange_agent:default"


async def test_invalid_confidence_raises():
    agent, _, _ = make_agent()
    with pytest.raises(ValueError):
        await agent.get_rate(make_intent(confidence=1.5))


# ── Step-up gate (wired now; money movement is increment 2) ───────────────────


def test_step_up_gate_halts_money_movement_without_biometric():
    # White-box: the increment-1 read path never sets is_money_movement, but the
    # ADR-049 §D4 step-up gate is wired for the increment-2 money-movement path.
    from src.agents.fx_exchange_agent import _ActionContext

    agent, _, _ = make_agent()
    ctx = _ActionContext(
        intent_text="Buy 1 BTC",
        process_ref=ProcessRef(process_id="PROC-FX-ORDER", version="1"),
        correlation_id="corr-fx-2",
        confidence_score=0.99,
        triggering_event="order:BTC/EUR",
        success_action="PLACE_ORDER",
        op="ExchangePort.get_rate",
        request_cost=RequestCost(tokens=50, cost=Decimal("0.01")),
        compliance_result=ComplianceResult.PASS,
        is_money_movement=True,
        amount=Decimal("60000.00"),
        biometric_verified=False,
        human_reviewed_by=None,
    )
    ev = agent._evaluate(ctx)
    assert ev.proceed is False
    assert ev.requires_step_up is True
    assert ev.action_taken == "HALT_STEP_UP_REQUIRED"
