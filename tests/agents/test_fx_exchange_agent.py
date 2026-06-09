"""Tests for the ADR-049 FX/Exchange mask agent (src/agents/fx_exchange_agent.py).

Increment 1 covers the AUTO read/quote path (``ExchangePort.get_rate``): the
scope allow-list, the AUTO happy path, unresolved process_ref, cost-cap breach,
below-AUTO confidence halts, the compliance gate, and the lineage-per-action
obligation (ADR-046). Ports and the recorder are fakes — the agent is exercised
as pure governance logic with no live infra; no real adapters are imported.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from src.agents.fx_exchange_agent import (
    AgentDecisionRecord,
    BudgetBreach,
    CancelOrderIntent,
    ComplianceResult,
    ConfirmationDecision,
    CostCap,
    CostWindow,
    DecisionRecorder,
    FXExchangeAgent,
    FXExchangeMask,
    OrderIntent,
    ProcessRef,
    RateQuoteIntent,
    RequestCost,
    SettlementInstruction,
)
from src.exchangeport.exchange_port import (
    AssetSymbol,
    ComplianceBlock,
    ExchangePort,
    ExchangeUnavailable,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderState,
    OrderType,
    RateQuote,
)
from src.wallet.wallet_port import ChainId, SeedMaterial, SignedTx, WalletPort

# ── Fakes (the ports & sink are injected interfaces; we never implement them in src) ──


class FakeRecorder(DecisionRecorder):
    def __init__(self) -> None:
        self.records: list[AgentDecisionRecord] = []

    async def record(self, record: AgentDecisionRecord) -> None:
        self.records.append(record)


class FakeExchangePort(ExchangePort):
    def __init__(
        self,
        quote: RateQuote | None = None,
        raises: Exception | None = None,
        *,
        order_result: OrderResult | None = None,
        order_raises: Exception | None = None,
        cancel_result: bool = True,
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
        self._order_result = order_result or OrderResult(
            order_id="ord-1", state=OrderState.ACCEPTED, filled_amount="0"
        )
        self._order_raises = order_raises
        self._cancel_result = cancel_result
        self.calls: list[tuple[AssetSymbol, AssetSymbol]] = []
        self.placed: list[OrderRequest] = []
        self.cancelled: list[str] = []

    async def get_rate(self, base: AssetSymbol, quote: AssetSymbol) -> RateQuote:
        self.calls.append((base, quote))
        if self._raises is not None:
            raise self._raises
        return self._quote

    async def place_order(self, order: OrderRequest) -> OrderResult:
        self.placed.append(order)
        if self._order_raises is not None:
            raise self._order_raises
        return self._order_result

    async def cancel_order(self, order_id: str) -> bool:
        self.cancelled.append(order_id)
        return self._cancel_result

    async def get_order_status(self, order_id: str) -> OrderResult:  # pragma: no cover
        raise NotImplementedError


class FakeWalletPort(WalletPort):
    """Injected for the settlement leg of a filled order; only ``sign_tx`` is
    exercised by the agent. The custody crypto itself is never implemented here."""

    def __init__(self, *, sign_raises: Exception | None = None) -> None:
        self._sign_raises = sign_raises
        self.signed: list[tuple[ChainId, str, object]] = []

    async def sign_tx(self, seed, chain, path, tx_payload):
        # The agent passes seed straight through to the custody adapter; we never
        # retain or log it here — only the non-secret call shape is recorded.
        self.signed.append((chain, path, tx_payload))
        if self._sign_raises is not None:
            raise self._sign_raises
        return SignedTx(chain=chain, raw_tx="0xsigned", tx_hash="0xhash")

    async def validate_address(self, chain: ChainId, address: str) -> bool:  # pragma: no cover
        raise NotImplementedError

    async def generate_seed_phrase(self, word_count):  # pragma: no cover
        raise NotImplementedError

    async def seed_phrase_to_entropy(self, phrase):  # pragma: no cover
        raise NotImplementedError

    async def derive_address(self, seed, chain, path):  # pragma: no cover
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
    wallet: FakeWalletPort | None = None,
) -> tuple[FXExchangeAgent, FakeExchangePort, FakeRecorder]:
    exchange = exchange or FakeExchangePort()
    recorder = recorder or FakeRecorder()
    agent = FXExchangeAgent(
        exchange_port=exchange,
        wallet_port=wallet or FakeWalletPort(),
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


# ── Reserve-before-dispatch under concurrency (S6.3 — cost-cap TOCTOU) ─────────


class _GatedExchangePort(FakeExchangePort):
    """Exchange port whose ``get_rate`` blocks on a shared gate so every dispatched
    action is in-flight simultaneously — forcing the concurrent per-window race the
    pre-fix check-then-add (debit after this await) could not contain."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()

    async def get_rate(self, base: AssetSymbol, quote: AssetSymbol) -> RateQuote:
        self.calls.append((base, quote))
        await self.gate.wait()
        return self._quote


class _RaceLosingWindow(CostWindow):
    """Passes the pre-flight ``_cost_breaches`` read-check (``used_*`` are zero) but
    LOSES the atomic reservation — simulating a concurrent action that drained the
    budget in the gap between ``_evaluate`` and ``try_reserve``."""

    def try_reserve(self, cost: RequestCost, cap: CostCap) -> bool:
        return False


async def test_concurrent_quotes_never_exceed_window_cap():
    # Window admits exactly 3 quotes of 0.20 (cap 0.60); eight concurrent same-window
    # reads all clear every other gate. Reserve-before-dispatch executes exactly 3 and
    # never exceeds the cap; the pre-fix check-then-add executed all 8 (fails here).
    mask = make_mask(
        cost_cap=CostCap(
            max_request_tokens=10_000,
            max_request_cost=Decimal("1.00"),
            max_window_tokens=10_000_000,
            max_window_cost=Decimal("0.60"),
        )
    )
    window = CostWindow()
    exchange = _GatedExchangePort()
    agent, _, recorder = make_agent(mask=mask, exchange=exchange, cost_window=window)
    cost = RequestCost(tokens=1, cost=Decimal("0.20"))
    n = 8
    task = asyncio.gather(
        *(agent.get_rate(make_intent(cost=cost, confidence=0.95)) for _ in range(n))
    )
    for _ in range(5):
        await asyncio.sleep(0)
    exchange.gate.set()
    outcomes = await task

    executed = [o for o in outcomes if o.executed]
    blocked = [o for o in outcomes if not o.executed]
    assert len(executed) == 3
    assert len(exchange.calls) == 3  # only reserved actions dispatched
    assert window.used_cost == Decimal("0.60")
    assert window.used_cost <= mask.cost_cap.max_window_cost
    assert all(o.halt_reason == "cost_cap_breach" for o in blocked)
    assert len(recorder.records) == n


async def test_lost_reservation_race_is_cost_cap_breach():
    # The reserve-failed branch: pre-flight check passes but the atomic reservation
    # loses the race → cost-cap BREACH, BLOCK, no port call, exactly one record.
    window = _RaceLosingWindow()
    agent, exchange, recorder = make_agent(cost_window=window)
    outcome = await agent.get_rate(make_intent(confidence=0.95))

    assert outcome.decision is ConfirmationDecision.BLOCK
    assert outcome.executed is False
    assert outcome.halt_reason == "cost_cap_breach"
    assert exchange.calls == []
    assert len(recorder.records) == 1
    assert recorder.records[0].budget_breach_flag is BudgetBreach.BREACH
    assert recorder.records[0].action_taken == "HALT_COST_CAP_BREACH"


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


# ── Increment 2: money-movement path (place_order / cancel_order / settlement) ──

SEED = SeedMaterial(entropy=b"\x00" * 16)


def make_order(**overrides) -> OrderRequest:
    base = {
        "base_asset": "BTC",
        "quote_asset": "EUR",
        "side": OrderSide.BUY,
        "type": OrderType.MARKET,
        "amount": "0.5",
        "client_order_id": "cli-ord-1",
        "correlation_id": "corr-fx-ord",
    }
    base.update(overrides)
    return OrderRequest(**base)


def make_settlement() -> SettlementInstruction:
    return SettlementInstruction(
        chain=ChainId.BTC,
        seed=SEED,
        derivation_path="m/44'/0'/0'/0/0",
        tx_payload={"to": "bc1qclient", "amount": "0.5"},
    )


def make_order_intent(
    *,
    confidence: float = 0.95,
    biometric_verified: bool = True,
    settlement: SettlementInstruction | None = None,
    order: OrderRequest | None = None,
    cost: RequestCost | None = None,
    process_ref: ProcessRef | None = None,
) -> OrderIntent:
    return OrderIntent(
        intent_text="Buy 0.5 BTC with EUR",
        process_ref=process_ref or ProcessRef(process_id="PROC-FX-ORDER", version="1"),
        order=order or make_order(),
        correlation_id="corr-fx-ord",
        confidence_score=confidence,
        request_cost=cost or RequestCost(tokens=80, cost=Decimal("0.05")),
        biometric_verified=biometric_verified,
        settlement=settlement,
    )


def make_cancel_intent(
    *,
    confidence: float = 0.95,
    order_id: str = "ord-1",
    process_ref: ProcessRef | None = None,
) -> CancelOrderIntent:
    return CancelOrderIntent(
        intent_text="Cancel my open BTC order",
        process_ref=process_ref or ProcessRef(process_id="PROC-FX-CANCEL", version="1"),
        order_id=order_id,
        correlation_id="corr-fx-cancel",
        confidence_score=confidence,
        request_cost=RequestCost(tokens=30, cost=Decimal("0.01")),
    )


def filled(**overrides) -> OrderResult:
    base = {
        "order_id": "ord-1",
        "state": OrderState.FILLED,
        "filled_amount": "0.5",
        "average_price": "60000.00",
    }
    base.update(overrides)
    return OrderResult(**base)


# ── place_order: AUTO band + biometric step-up ────────────────────────────────


async def test_place_order_auto_with_step_up_executes():
    agent, exchange, recorder = make_agent()
    outcome = await agent.place_order(make_order_intent(confidence=0.95, biometric_verified=True))

    assert outcome.decision is ConfirmationDecision.AUTO
    assert outcome.executed is True
    assert isinstance(outcome.result, OrderResult)
    assert len(exchange.placed) == 1
    assert exchange.placed[0].client_order_id == "cli-ord-1"
    assert recorder.records[0].action_taken == "PLACE_ORDER"
    assert len(recorder.records) == 1


async def test_place_order_biometric_required_halts_even_at_auto():
    # Critical money movement requires biometric step-up regardless of confidence.
    agent, exchange, recorder = make_agent()
    outcome = await agent.place_order(make_order_intent(confidence=0.99, biometric_verified=False))

    assert outcome.executed is False
    assert outcome.requires_step_up is True
    assert outcome.halt_reason == "step_up_required"
    assert exchange.placed == []
    assert recorder.records[0].action_taken == "HALT_STEP_UP_REQUIRED"
    assert len(recorder.records) == 1


# ── place_order: REVIEW band → HITL hold then proceed ─────────────────────────


async def test_place_order_review_holds_for_hitl():
    agent, exchange, recorder = make_agent()
    outcome = await agent.place_order(make_order_intent(confidence=0.80, biometric_verified=True))

    assert outcome.decision is ConfirmationDecision.REVIEW
    assert outcome.executed is False
    assert outcome.requires_hitl is True
    assert outcome.halt_reason == "hitl_review_required"
    assert exchange.placed == []
    assert recorder.records[0].action_taken == "HOLD_FOR_REVIEW"


async def test_place_order_review_proceeds_with_reviewer():
    agent, exchange, recorder = make_agent()
    outcome = await agent.place_order(
        make_order_intent(confidence=0.80, biometric_verified=True),
        human_reviewed_by="mlro-anna",
    )

    assert outcome.decision is ConfirmationDecision.REVIEW
    assert outcome.executed is True
    assert len(exchange.placed) == 1
    assert recorder.records[0].action_taken == "PLACE_ORDER"
    assert recorder.records[0].human_reviewed_by == "mlro-anna"


# ── place_order: BLOCK / cost-cap / compliance / scope ────────────────────────


async def test_place_order_block_low_confidence():
    agent, exchange, recorder = make_agent()
    outcome = await agent.place_order(make_order_intent(confidence=0.50))

    assert outcome.decision is ConfirmationDecision.BLOCK
    assert outcome.executed is False
    assert outcome.halt_reason == "low_confidence"
    assert exchange.placed == []
    assert recorder.records[0].action_taken == "BLOCK_LOW_CONFIDENCE"


async def test_place_order_cost_cap_breach_halts():
    agent, exchange, recorder = make_agent()
    outcome = await agent.place_order(
        make_order_intent(cost=RequestCost(tokens=999_999, cost=Decimal("0.01")))
    )

    assert outcome.decision is ConfirmationDecision.BLOCK
    assert outcome.halt_reason == "cost_cap_breach"
    assert exchange.placed == []
    assert recorder.records[0].budget_breach_flag is BudgetBreach.BREACH


async def test_place_order_compliance_fail_halts():
    agent, exchange, recorder = make_agent()
    outcome = await agent.place_order(make_order_intent(), compliance_result=ComplianceResult.FAIL)

    assert outcome.decision is ConfirmationDecision.BLOCK
    assert outcome.executed is False
    assert exchange.placed == []
    assert recorder.records[0].action_taken == "HALT_COMPLIANCE_BLOCK"
    assert recorder.records[0].compliance_result is ComplianceResult.FAIL


async def test_place_order_out_of_scope_rejected():
    agent, exchange, recorder = make_agent(mask=make_mask(scope=("ExchangePort.get_rate",)))
    outcome = await agent.place_order(make_order_intent())

    assert outcome.decision is ConfirmationDecision.BLOCK
    assert outcome.halt_reason == "out_of_scope"
    assert exchange.placed == []
    assert recorder.records[0].action_taken == "REJECT_OUT_OF_SCOPE"


# ── place_order: idempotency + port error ─────────────────────────────────────


async def test_place_order_idempotent_replay_returns_cached():
    exchange = FakeExchangePort(order_result=filled())
    agent, exchange, recorder = make_agent(exchange=exchange)
    first = await agent.place_order(make_order_intent())
    second = await agent.place_order(make_order_intent())

    assert second is first  # replay returns the recorded outcome, no re-placement
    assert len(exchange.placed) == 1
    assert len(recorder.records) == 1


async def test_place_order_port_error_records_then_raises():
    exchange = FakeExchangePort(order_raises=ExchangeUnavailable("upstream 503"))
    agent, exchange, recorder = make_agent(exchange=exchange)
    with pytest.raises(ExchangeUnavailable):
        await agent.place_order(make_order_intent())

    assert len(exchange.placed) == 1
    assert len(recorder.records) == 1
    assert recorder.records[0].action_taken == "HALT_EXCHANGE_ERROR:ExchangeUnavailable"


async def test_place_order_compliance_block_from_port_records_then_raises():
    exchange = FakeExchangePort(order_raises=ComplianceBlock("sanctioned counterparty"))
    agent, exchange, recorder = make_agent(exchange=exchange)
    with pytest.raises(ComplianceBlock):
        await agent.place_order(make_order_intent())

    assert recorder.records[0].compliance_result is ComplianceResult.FAIL
    assert recorder.records[0].action_taken.startswith("HALT_EXCHANGE_ERROR:")


# ── WalletPort settlement leg ─────────────────────────────────────────────────


async def test_settlement_leg_invoked_on_fill():
    wallet = FakeWalletPort()
    exchange = FakeExchangePort(order_result=filled())
    agent, exchange, recorder = make_agent(exchange=exchange, wallet=wallet)
    outcome = await agent.place_order(make_order_intent(settlement=make_settlement()))

    assert outcome.executed is True
    assert len(wallet.signed) == 1
    assert wallet.signed[0][0] is ChainId.BTC
    assert wallet.signed[0][1] == "m/44'/0'/0'/0/0"
    assert len(recorder.records) == 1  # settlement is part of the single place_order action


async def test_no_settlement_when_order_not_filled():
    wallet = FakeWalletPort()
    exchange = FakeExchangePort(order_result=OrderResult("ord-1", OrderState.ACCEPTED, "0"))
    agent, exchange, recorder = make_agent(exchange=exchange, wallet=wallet)
    outcome = await agent.place_order(make_order_intent(settlement=make_settlement()))

    assert outcome.executed is True
    assert wallet.signed == []  # only a FILLED order settles


async def test_no_settlement_without_instruction():
    wallet = FakeWalletPort()
    exchange = FakeExchangePort(order_result=filled())
    agent, exchange, recorder = make_agent(exchange=exchange, wallet=wallet)
    outcome = await agent.place_order(make_order_intent(settlement=None))

    assert outcome.executed is True
    assert wallet.signed == []


async def test_settlement_failure_records_then_raises():
    wallet = FakeWalletPort(sign_raises=RuntimeError("HSM offline"))
    exchange = FakeExchangePort(order_result=filled())
    agent, exchange, recorder = make_agent(exchange=exchange, wallet=wallet)
    with pytest.raises(RuntimeError):
        await agent.place_order(make_order_intent(settlement=make_settlement()))

    # The whole action still owes exactly one lineage record.
    assert len(recorder.records) == 1
    assert recorder.records[0].action_taken == "HALT_SETTLEMENT_ERROR:RuntimeError"


# ── cancel_order: idempotent + gate chain + HITL ──────────────────────────────


async def test_cancel_order_accepted_returns_true():
    agent, exchange, recorder = make_agent(exchange=FakeExchangePort(cancel_result=True))
    outcome = await agent.cancel_order(make_cancel_intent())

    assert outcome.executed is True
    assert outcome.result is True
    assert exchange.cancelled == ["ord-1"]
    assert recorder.records[0].action_taken == "CANCEL_ORDER"


async def test_cancel_order_idempotent_final_state_returns_false():
    # Port idempotency: cancelling an already-final order returns False, never raises.
    exchange = FakeExchangePort(cancel_result=False)
    agent, exchange, recorder = make_agent(exchange=exchange)
    first = await agent.cancel_order(make_cancel_intent())
    second = await agent.cancel_order(make_cancel_intent())

    assert first.result is False
    assert second.result is False
    assert exchange.cancelled == ["ord-1", "ord-1"]
    assert len(recorder.records) == 2  # one record per action, both safe


async def test_cancel_order_no_biometric_step_up():
    # Cancellation is not critical money movement → no step-up even without biometric.
    agent, exchange, recorder = make_agent()
    outcome = await agent.cancel_order(make_cancel_intent(confidence=0.95))

    assert outcome.executed is True
    assert outcome.requires_step_up is False
    assert recorder.records[0].action_taken == "CANCEL_ORDER"


async def test_cancel_order_review_holds_then_proceeds():
    agent, exchange, recorder = make_agent()
    hold = await agent.cancel_order(make_cancel_intent(confidence=0.80))
    assert hold.executed is False
    assert hold.halt_reason == "hitl_review_required"
    assert exchange.cancelled == []

    proceed = await agent.cancel_order(
        make_cancel_intent(confidence=0.80), human_reviewed_by="ops-lead"
    )
    assert proceed.executed is True
    assert exchange.cancelled == ["ord-1"]


# ── Lineage obligation across the new money-movement paths (ADR-046) ──────────


async def test_lineage_one_record_per_money_movement_action():
    wallet = FakeWalletPort()
    exchange = FakeExchangePort(order_result=filled())
    agent, exchange, recorder = make_agent(exchange=exchange, wallet=wallet)

    await agent.place_order(make_order_intent(confidence=0.50))  # halt: low confidence
    await agent.place_order(make_order_intent(settlement=make_settlement()))  # execute + settle
    await agent.cancel_order(make_cancel_intent())  # execute

    assert len(recorder.records) == 3
    for rec in recorder.records:
        assert rec.record_id
        assert rec.timestamp.tzinfo is not None
        assert rec.agent_id == "fx_exchange_agent"
        assert rec.policies_evaluated
        assert rec.budget_window_ref == "fx_exchange_agent:default"
