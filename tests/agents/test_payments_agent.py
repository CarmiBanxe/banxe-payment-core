"""Tests for the ADR-049 Payments mask agent (src/agents/payments_agent.py).

Covers every mask path: AUTO execute (read + money movement), REVIEW→HITL,
BLOCK (low confidence), cost-cap breach (per-request and per-window), biometric
step-up required, compliance block, unresolved process_ref, and the
lineage-per-action obligation (ADR-046). Ports and the recorder are fakes — the
agent is exercised as pure governance logic with no live infra.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.agents.payments_agent import (
    AddressValidationIntent,
    AgentDecisionRecord,
    BudgetBreach,
    ComplianceResult,
    ConfirmationDecision,
    CostCap,
    CostWindow,
    DecisionRecorder,
    IdempotencyStorePort,
    InMemoryIdempotencyStore,
    PaymentIntent,
    PaymentsAgent,
    PaymentsMask,
    ProcessRef,
    RequestCost,
)
from src.partner.partner_port import (
    ComplianceBlock,
    PartnerAccountId,
    PartnerPort,
    PartnerType,
    PaymentInstruction,
    PaymentResult,
    PaymentStatus,
)
from src.wallet.wallet_port import ChainId, WalletPort

# ── Fakes (the ports & sink are injected interfaces; we never implement them in src) ──


class FakeRecorder(DecisionRecorder):
    def __init__(self) -> None:
        self.records: list[AgentDecisionRecord] = []

    async def record(self, record: AgentDecisionRecord) -> None:
        self.records.append(record)


class FakePartnerPort(PartnerPort):
    def __init__(
        self, result: PaymentResult | None = None, raises: Exception | None = None
    ) -> None:
        self._result = result or PaymentResult(status=PaymentStatus.ACCEPTED, partner_tx_id="ptx_1")
        self._raises = raises
        self.calls: list[PaymentInstruction] = []

    async def initiate_payment(self, instruction: PaymentInstruction) -> PaymentResult:
        self.calls.append(instruction)
        if self._raises is not None:
            raise self._raises
        return self._result

    async def get_payment_status(self, partner_tx_id: str) -> PaymentResult:  # pragma: no cover
        raise NotImplementedError

    async def reconcile(self, date: str):  # pragma: no cover
        raise NotImplementedError


class FakeWalletPort(WalletPort):
    def __init__(self, valid: bool = True) -> None:
        self._valid = valid
        self.validated: list[tuple[ChainId, str]] = []

    async def validate_address(self, chain: ChainId, address: str) -> bool:
        self.validated.append((chain, address))
        return self._valid

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


def make_mask(**overrides) -> PaymentsMask:
    base = {
        "cost_cap": CostCap(
            max_request_tokens=10_000,
            max_request_cost=Decimal("1.00"),
            max_window_tokens=100_000,
            max_window_cost=Decimal("10.00"),
        ),
        "step_up_materiality": Decimal("100.00"),
    }
    base.update(overrides)
    return PaymentsMask(**base)


def make_instruction(
    amount: str = "500.00", *, idempotency_key: str = "idem-42"
) -> PaymentInstruction:
    return PaymentInstruction(
        from_account=PartnerAccountId(partner=PartnerType.SEPA, external_account_id="acct_1"),
        to_iban="GB29NWBK60161331926819",
        amount=amount,
        currency="EUR",
        reference="invoice-42",
        idempotency_key=idempotency_key,
        correlation_id="corr-42",
    )


def make_intent(
    *,
    amount: str = "500.00",
    confidence: float = 0.95,
    biometric: bool = True,
    cost: RequestCost | None = None,
    process_ref: ProcessRef | None = None,
    idempotency_key: str = "idem-42",
) -> PaymentIntent:
    return PaymentIntent(
        intent_text="Pay 500 EUR to my landlord",
        process_ref=process_ref or ProcessRef(process_id="PROC-PAYMENT-SEPA", version="1"),
        instruction=make_instruction(amount, idempotency_key=idempotency_key),
        correlation_id="corr-42",
        confidence_score=confidence,
        request_cost=cost or RequestCost(tokens=500, cost=Decimal("0.05")),
        biometric_verified=biometric,
    )


def make_agent(
    *,
    mask: PaymentsMask | None = None,
    partner: FakePartnerPort | None = None,
    wallet: FakeWalletPort | None = None,
    recorder: FakeRecorder | None = None,
    cost_window: CostWindow | None = None,
    idempotency_store: IdempotencyStorePort | None = None,
) -> tuple[PaymentsAgent, FakePartnerPort, FakeWalletPort, FakeRecorder]:
    partner = partner or FakePartnerPort()
    wallet = wallet or FakeWalletPort()
    recorder = recorder or FakeRecorder()
    agent = PaymentsAgent(
        wallet_port=wallet,
        partner_port=partner,
        recorder=recorder,
        mask=mask or make_mask(),
        cost_window=cost_window,
        idempotency_store=idempotency_store,
    )
    return agent, partner, wallet, recorder


# ── AUTO path ───────────────────────────────────────────────────────────────


async def test_auto_payment_with_biometric_executes():
    agent, partner, _, recorder = make_agent()
    outcome = await agent.initiate_payment(make_intent(confidence=0.95, biometric=True))

    assert outcome.decision is ConfirmationDecision.AUTO
    assert outcome.executed is True
    assert len(partner.calls) == 1
    assert outcome.result is not None
    assert outcome.record.action_taken == "APPROVE_PAYMENT"
    assert len(recorder.records) == 1


async def test_auto_read_validate_address_no_step_up():
    agent, _, wallet, recorder = make_agent()
    intent = AddressValidationIntent(
        intent_text="Is this BTC address valid?",
        process_ref=ProcessRef(process_id="PROC-ADDR-CHECK", version="1"),
        chain=ChainId.BTC,
        address="bc1qexample",
        correlation_id="corr-read",
        confidence_score=0.99,
        request_cost=RequestCost(tokens=50, cost=Decimal("0.01")),
    )
    outcome = await agent.validate_address(intent)

    assert outcome.decision is ConfirmationDecision.AUTO
    assert outcome.executed is True
    assert outcome.result is True
    assert outcome.requires_step_up is False
    assert wallet.validated == [(ChainId.BTC, "bc1qexample")]
    assert recorder.records[0].action_taken == "VALIDATE_ADDRESS"


async def test_sub_materiality_payment_skips_step_up():
    # Amount below mask materiality → money movement but no step-up required.
    agent, partner, _, _ = make_agent()
    outcome = await agent.initiate_payment(
        make_intent(amount="50.00", confidence=0.95, biometric=False)
    )
    assert outcome.executed is True
    assert len(partner.calls) == 1


# ── REVIEW / HITL path ────────────────────────────────────────────────────────


async def test_review_band_pauses_for_hitl():
    agent, partner, _, recorder = make_agent()
    outcome = await agent.initiate_payment(make_intent(confidence=0.80))

    assert outcome.decision is ConfirmationDecision.REVIEW
    assert outcome.executed is False
    assert outcome.requires_hitl is True
    assert partner.calls == []
    assert recorder.records[0].action_taken == "HOLD_FOR_REVIEW"
    assert recorder.records[0].human_reviewed_by is None


async def test_review_with_human_reviewer_proceeds():
    agent, partner, _, recorder = make_agent()
    outcome = await agent.initiate_payment(
        make_intent(confidence=0.80, biometric=True), human_reviewed_by="mlro@banxe"
    )
    assert outcome.decision is ConfirmationDecision.REVIEW
    assert outcome.executed is True
    assert len(partner.calls) == 1
    assert recorder.records[0].human_reviewed_by == "mlro@banxe"
    assert recorder.records[0].action_taken == "APPROVE_PAYMENT"


# ── BLOCK path ────────────────────────────────────────────────────────────────


async def test_block_low_confidence():
    agent, partner, _, recorder = make_agent()
    outcome = await agent.initiate_payment(make_intent(confidence=0.50))

    assert outcome.decision is ConfirmationDecision.BLOCK
    assert outcome.executed is False
    assert outcome.requires_hitl is True
    assert partner.calls == []
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
    agent, _, _, _ = make_agent()
    outcome = await agent.initiate_payment(
        make_intent(confidence=confidence, biometric=True), human_reviewed_by="mlro@banxe"
    )
    assert outcome.decision is expected


# ── Cost-cap breach ───────────────────────────────────────────────────────────


async def test_per_request_cost_cap_breach_blocks():
    agent, partner, _, recorder = make_agent()
    intent = make_intent(cost=RequestCost(tokens=999_999, cost=Decimal("0.01")))
    outcome = await agent.initiate_payment(intent)

    assert outcome.decision is ConfirmationDecision.BLOCK
    assert outcome.executed is False
    assert outcome.halt_reason == "cost_cap_breach"
    assert partner.calls == []
    assert recorder.records[0].budget_breach_flag is BudgetBreach.BREACH
    assert recorder.records[0].action_taken == "HALT_COST_CAP_BREACH"


async def test_per_window_cost_cap_breach_blocks():
    window = CostWindow(used_tokens=99_900, used_cost=Decimal("0.00"))
    agent, partner, _, _ = make_agent(cost_window=window)
    intent = make_intent(cost=RequestCost(tokens=200, cost=Decimal("0.01")))
    outcome = await agent.initiate_payment(intent)

    assert outcome.decision is ConfirmationDecision.BLOCK
    assert outcome.halt_reason == "cost_cap_breach"
    assert partner.calls == []


async def test_window_accumulates_on_successful_action():
    window = CostWindow()
    agent, _, _, _ = make_agent(cost_window=window)
    await agent.initiate_payment(make_intent(cost=RequestCost(tokens=300, cost=Decimal("0.02"))))
    assert window.used_tokens == 300
    assert window.used_cost == Decimal("0.02")


# ── Biometric step-up ─────────────────────────────────────────────────────────


async def test_biometric_required_halts_even_at_auto_confidence():
    agent, partner, _, recorder = make_agent()
    outcome = await agent.initiate_payment(
        make_intent(amount="500.00", confidence=0.99, biometric=False)
    )
    assert outcome.decision is ConfirmationDecision.AUTO
    assert outcome.executed is False
    assert outcome.requires_step_up is True
    assert partner.calls == []
    assert recorder.records[0].action_taken == "HALT_STEP_UP_REQUIRED"
    assert "ADR-049-D4-biometric-step-up" in recorder.records[0].policies_evaluated


async def test_step_up_disabled_by_mask_config():
    # Config-as-data: a mask may disable money-movement step-up entirely.
    agent, partner, _, _ = make_agent(mask=make_mask(require_step_up_for_money_movement=False))
    outcome = await agent.initiate_payment(
        make_intent(amount="9999.00", confidence=0.95, biometric=False)
    )
    assert outcome.executed is True
    assert len(partner.calls) == 1


def test_step_up_fail_safe_when_amount_unknown():
    # Fail-safe: money movement with an unknown amount always demands step-up.
    from src.agents.payments_agent import _ActionContext

    agent, _, _, _ = make_agent()
    ctx = _ActionContext(
        intent_text="x",
        process_ref=ProcessRef(process_id="P", version="1"),
        correlation_id="c",
        confidence_score=0.99,
        triggering_event="t",
        success_action="APPROVE_PAYMENT",
        request_cost=RequestCost(tokens=1, cost=Decimal("0")),
        compliance_result=ComplianceResult.PASS,
        is_money_movement=True,
        amount=None,
        biometric_verified=False,
        human_reviewed_by=None,
    )
    assert agent._step_up_required(ctx) is True


# ── Compliance gate ───────────────────────────────────────────────────────────


async def test_compliance_fail_blocks():
    agent, partner, _, recorder = make_agent()
    outcome = await agent.initiate_payment(make_intent(), compliance_result=ComplianceResult.FAIL)
    assert outcome.decision is ConfirmationDecision.BLOCK
    assert outcome.executed is False
    assert partner.calls == []
    assert recorder.records[0].compliance_result is ComplianceResult.FAIL
    assert recorder.records[0].action_taken == "HALT_COMPLIANCE_BLOCK"


async def test_compliance_escalate_blocks():
    agent, partner, _, recorder = make_agent()
    outcome = await agent.initiate_payment(
        make_intent(), compliance_result=ComplianceResult.ESCALATE
    )
    assert outcome.decision is ConfirmationDecision.BLOCK
    assert outcome.executed is False
    assert partner.calls == []
    assert recorder.records[0].compliance_result is ComplianceResult.ESCALATE
    assert recorder.records[0].action_taken == "HALT_COMPLIANCE_BLOCK"


async def test_partner_compliance_block_records_then_raises():
    partner = FakePartnerPort(raises=ComplianceBlock("sanctioned beneficiary"))
    agent, partner, _, recorder = make_agent(partner=partner)
    with pytest.raises(ComplianceBlock):
        await agent.initiate_payment(make_intent(confidence=0.99, biometric=True))
    # Lineage emitted even on port failure.
    assert len(recorder.records) == 1
    assert recorder.records[0].compliance_result is ComplianceResult.FAIL
    assert recorder.records[0].action_taken.startswith("HALT_PARTNER_ERROR:")


# ── Process resolution (ADR-048) ──────────────────────────────────────────────


async def test_unresolved_process_ref_blocks():
    agent, partner, _, recorder = make_agent()
    intent = make_intent(process_ref=ProcessRef(process_id="", version=""))
    outcome = await agent.initiate_payment(intent)
    assert outcome.decision is ConfirmationDecision.BLOCK
    assert outcome.halt_reason == "unresolved_process_ref"
    assert partner.calls == []
    assert recorder.records[0].action_taken == "HALT_UNRESOLVED_PROCESS"


# ── Lineage obligation (ADR-046) ──────────────────────────────────────────────


async def test_lineage_record_emitted_per_action_with_adr046_fields():
    agent, _, _, recorder = make_agent()
    # Distinct idempotency keys: each is its own logical action, so the second
    # (low-confidence) call is a genuine halt — not an idempotent replay.
    await agent.initiate_payment(make_intent(idempotency_key="idem-a"))
    await agent.initiate_payment(make_intent(idempotency_key="idem-b", confidence=0.50))
    assert len(recorder.records) == 2  # one per action, including the halt
    assert recorder.records[1].action_taken == "BLOCK_LOW_CONFIDENCE"

    rec = recorder.records[0]
    assert rec.record_id
    assert rec.timestamp.tzinfo is not None
    assert rec.agent_id == "payments_agent"
    assert rec.intent == "Pay 500 EUR to my landlord"
    assert rec.correlation_id == "corr-42"
    assert rec.policies_evaluated  # non-empty ordered policy list
    assert 0.0 <= rec.confidence_score <= 1.0
    assert rec.cost_tokens == 500
    assert rec.cost_amount == Decimal("0.05")
    assert rec.budget_window_ref == "payments_agent:default"


async def test_invalid_confidence_raises():
    agent, _, _, _ = make_agent()
    with pytest.raises(ValueError):
        await agent.initiate_payment(make_intent(confidence=1.5))


# ── Idempotent replay protection (S5.3 — DOUBLE-PAYMENT class) ─────────────────


async def test_duplicate_idempotency_key_suppresses_second_payment():
    # A retried/double-submitted intent with an already-executed idempotency_key
    # must NOT move funds twice: the port executes once, the replay is suppressed
    # and returns the prior result, and each call still emits exactly one record.
    agent, partner, _, recorder = make_agent()

    first = await agent.initiate_payment(make_intent())
    assert first.executed is True
    assert len(partner.calls) == 1

    second = await agent.initiate_payment(make_intent())  # same idempotency_key
    assert second.executed is False
    assert second.decision is ConfirmationDecision.AUTO
    assert second.halt_reason == "duplicate_suppressed"
    assert second.record.action_taken == "DUPLICATE_SUPPRESSED"
    assert second.result == first.result  # prior result returned

    assert len(partner.calls) == 1  # port executed ONCE — no double-payment
    assert len(recorder.records) == 2  # exactly one record per call
    assert recorder.records[1].action_taken == "DUPLICATE_SUPPRESSED"
    assert recorder.records[1].compliance_result is ComplianceResult.NA


async def test_distinct_idempotency_keys_both_execute():
    agent, partner, _, _ = make_agent()
    await agent.initiate_payment(make_intent(idempotency_key="key-1"))
    await agent.initiate_payment(make_intent(idempotency_key="key-2"))
    assert len(partner.calls) == 2  # distinct keys → both move funds


async def test_suppressed_duplicate_does_not_consume_budget():
    # A suppressed replay must not touch the per-window budget: only the original
    # execution accrues cost (ADR-047).
    window = CostWindow()
    agent, _, _, _ = make_agent(cost_window=window)
    await agent.initiate_payment(make_intent(cost=RequestCost(tokens=300, cost=Decimal("0.02"))))
    await agent.initiate_payment(make_intent(cost=RequestCost(tokens=300, cost=Decimal("0.02"))))
    assert window.used_tokens == 300  # second (suppressed) added nothing
    assert window.used_cost == Decimal("0.02")


async def test_failed_payment_not_marked_allows_retry():
    # Mark-after-success: a payment that never executed (here, blocked on low
    # confidence) does not claim its key, so a corrected retry still runs.
    agent, partner, _, _ = make_agent()
    blocked = await agent.initiate_payment(
        make_intent(idempotency_key="retry-key", confidence=0.50)
    )
    assert blocked.executed is False
    assert partner.calls == []

    retried = await agent.initiate_payment(
        make_intent(idempotency_key="retry-key", confidence=0.95, biometric=True)
    )
    assert retried.executed is True
    assert len(partner.calls) == 1  # the retry was NOT suppressed


async def test_injected_idempotency_store_is_used():
    store = InMemoryIdempotencyStore()
    agent, partner, _, _ = make_agent(idempotency_store=store)
    outcome = await agent.initiate_payment(make_intent(idempotency_key="wired-key"))
    assert outcome.executed is True
    assert await store.has_seen("wired-key") is True
    assert await store.prior_result("wired-key") == outcome.result
