"""Tests for the ADR-049 Wallet mask agent (src/agents/wallet_agent.py).

Covers both contours of the Mixed-autonomy Wallet mask:

* Read contour (``validate_address`` / ``verify_signature`` / ``derive_address``):
  the scope allow-list, the AUTO happy path, the PII overlay, below-AUTO re-check
  halt, cost-cap, low-confidence BLOCK, unresolved process_ref.
* Sensitive contour (``generate_seed_phrase`` / ``seed_phrase_to_entropy`` /
  ``sign_tx`` / ``encrypt`` / ``decrypt``): REVIEW-bias, mandatory biometric
  step-up regardless of confidence, the AML overlay, HITL hold-then-proceed.

Plus the lineage-per-action obligation (ADR-046) and the R-SEC guarantee: NO
secret material (seed / entropy / mnemonic / password / plaintext) ever appears in
an emitted ``AgentDecisionRecord``. Ports and the recorder are fakes — the agent
is exercised as pure governance logic with no live infra; no real adapters import.
"""

from __future__ import annotations

import asyncio
import dataclasses
from decimal import Decimal

import pytest

from src.agents.wallet_agent import (
    AgentDecisionRecord,
    BudgetBreach,
    ComplianceResult,
    ConfirmationDecision,
    CostCap,
    CostWindow,
    DecisionRecorder,
    DecryptIntent,
    DeriveAddressIntent,
    EncryptIntent,
    GenerateSeedPhraseIntent,
    ProcessRef,
    RequestCost,
    SeedToEntropyIntent,
    SignTxIntent,
    ValidateAddressIntent,
    VerifySignatureIntent,
    WalletAgent,
    WalletMask,
)
from src.wallet.wallet_port import (
    ChainId,
    SeedMaterial,
    SignedTx,
    WalletAddress,
    WalletPort,
)

# ── Secret sentinels — these must NEVER leak into a lineage record (R-SEC) ──────

SECRET_PHRASE = "SENTINEL-MNEMONIC-abandon-ability-able-zoo"
SECRET_PASSWORD = "SENTINEL-P@ssw0rd-do-not-log"  # nosec B105 — test sentinel, not a real cred
SECRET_PLAINTEXT = "SENTINEL-PLAINTEXT-private-note"
SECRET_SEED_ENTROPY = b"SENTINEL-SEED-ENTROPY-BYTES--16b"
GENERATED_MNEMONIC = "SENTINEL-GENERATED-MNEMONIC-twelve-words"
DECRYPTED_PLAINTEXT = "SENTINEL-DECRYPTED-PLAINTEXT-out"
DERIVED_ENTROPY = b"SENTINEL-DERIVED-ENTROPY-FROM-PHR"

SEED = SeedMaterial(entropy=SECRET_SEED_ENTROPY)

ALL_SECRETS: tuple[str, ...] = (
    SECRET_PHRASE,
    SECRET_PASSWORD,
    SECRET_PLAINTEXT,
    GENERATED_MNEMONIC,
    DECRYPTED_PLAINTEXT,
    SECRET_SEED_ENTROPY.decode(),
    DERIVED_ENTROPY.decode(),
)


# ── Fakes (the port & sink are injected interfaces; never implemented in src) ──


class FakeRecorder(DecisionRecorder):
    def __init__(self) -> None:
        self.records: list[AgentDecisionRecord] = []

    async def record(self, record: AgentDecisionRecord) -> None:
        self.records.append(record)


class FakeWalletPort(WalletPort):
    """Records the (non-secret-asserting) call shape and returns sentinel secrets so
    the tests can prove those secrets never reach the lineage record."""

    def __init__(self, *, raises: Exception | None = None) -> None:
        self._raises = raises
        self.calls: list[str] = []

    def _maybe_raise(self) -> None:
        if self._raises is not None:
            raise self._raises

    async def generate_seed_phrase(self, word_count):
        self.calls.append(f"generate_seed_phrase:{word_count}")
        self._maybe_raise()
        return GENERATED_MNEMONIC

    async def seed_phrase_to_entropy(self, phrase):
        self.calls.append("seed_phrase_to_entropy")
        self._maybe_raise()
        return SeedMaterial(entropy=DERIVED_ENTROPY)

    async def derive_address(self, seed, chain, path):
        self.calls.append(f"derive_address:{chain.value}:{path}")
        self._maybe_raise()
        return WalletAddress(
            chain=chain, address="bc1qpublic", derivation_path=path, public_key="0xpub"
        )

    async def sign_tx(self, seed, chain, path, tx_payload):
        self.calls.append(f"sign_tx:{chain.value}:{path}")
        self._maybe_raise()
        return SignedTx(chain=chain, raw_tx="0xsigned", tx_hash="0xhash")

    async def verify_signature(self, chain, address, message, signature):
        self.calls.append(f"verify_signature:{chain.value}:{address}")
        self._maybe_raise()
        return True

    async def validate_address(self, chain, address):
        self.calls.append(f"validate_address:{chain.value}:{address}")
        self._maybe_raise()
        return True

    async def encrypt(self, plaintext, password):
        self.calls.append("encrypt")
        self._maybe_raise()
        return "OPAQUE-CIPHERTEXT"

    async def decrypt(self, ciphertext, password):
        self.calls.append("decrypt")
        self._maybe_raise()
        return DECRYPTED_PLAINTEXT


# ── Builders ──────────────────────────────────────────────────────────────────


def make_mask(**overrides) -> WalletMask:
    base = {
        "cost_cap": CostCap(
            max_request_tokens=10_000,
            max_request_cost=Decimal("1.00"),
            max_window_tokens=100_000,
            max_window_cost=Decimal("10.00"),
        ),
    }
    base.update(overrides)
    return WalletMask(**base)


def make_agent(
    *,
    mask: WalletMask | None = None,
    wallet: FakeWalletPort | None = None,
    recorder: FakeRecorder | None = None,
    cost_window: CostWindow | None = None,
) -> tuple[WalletAgent, FakeWalletPort, FakeRecorder]:
    wallet = wallet or FakeWalletPort()
    recorder = recorder or FakeRecorder()
    agent = WalletAgent(
        wallet_port=wallet,
        recorder=recorder,
        mask=mask or make_mask(),
        cost_window=cost_window,
    )
    return agent, wallet, recorder


_REF = ProcessRef(process_id="PROC-WALLET", version="1")


def _common(confidence: float, cost: RequestCost | None, ref: ProcessRef | None) -> dict:
    return {
        "process_ref": ref or _REF,
        "correlation_id": "corr-w-1",
        "confidence_score": confidence,
        "request_cost": cost or RequestCost(tokens=50, cost=Decimal("0.01")),
    }


def validate_intent(*, confidence=0.95, cost=None, ref=None) -> ValidateAddressIntent:
    return ValidateAddressIntent(
        intent_text="Is this a valid BTC address?",
        chain=ChainId.BTC,
        address="bc1qpublic",
        **_common(confidence, cost, ref),
    )


def verify_intent(*, confidence=0.95, cost=None, ref=None) -> VerifySignatureIntent:
    return VerifySignatureIntent(
        intent_text="Did this address sign this message?",
        chain=ChainId.ETH,
        address="0xpublic",
        message="hello",
        signature="0xsig",
        **_common(confidence, cost, ref),
    )


def derive_intent(*, confidence=0.95, cost=None, ref=None) -> DeriveAddressIntent:
    return DeriveAddressIntent(
        intent_text="Derive my receive address",
        seed=SEED,
        chain=ChainId.BTC,
        path="m/44'/0'/0'/0/0",
        **_common(confidence, cost, ref),
    )


def gen_seed_intent(
    *, confidence=0.95, biometric=True, cost=None, ref=None
) -> GenerateSeedPhraseIntent:
    return GenerateSeedPhraseIntent(
        intent_text="Create a new wallet",
        word_count=12,
        biometric_verified=biometric,
        **_common(confidence, cost, ref),
    )


def seed_entropy_intent(
    *, confidence=0.95, biometric=True, cost=None, ref=None
) -> SeedToEntropyIntent:
    return SeedToEntropyIntent(
        intent_text="Restore wallet from phrase",
        phrase=SECRET_PHRASE,
        biometric_verified=biometric,
        **_common(confidence, cost, ref),
    )


def sign_intent(*, confidence=0.95, biometric=True, cost=None, ref=None) -> SignTxIntent:
    return SignTxIntent(
        intent_text="Sign my withdrawal",
        seed=SEED,
        chain=ChainId.BTC,
        path="m/44'/0'/0'/0/0",
        tx_payload={"to": "bc1qdest", "amount": "0.5"},
        biometric_verified=biometric,
        **_common(confidence, cost, ref),
    )


def encrypt_intent(*, confidence=0.95, biometric=True, cost=None, ref=None) -> EncryptIntent:
    return EncryptIntent(
        intent_text="Encrypt my backup",
        plaintext=SECRET_PLAINTEXT,
        password=SECRET_PASSWORD,
        biometric_verified=biometric,
        **_common(confidence, cost, ref),
    )


def decrypt_intent(*, confidence=0.95, biometric=True, cost=None, ref=None) -> DecryptIntent:
    return DecryptIntent(
        intent_text="Decrypt my backup",
        ciphertext="OPAQUE-CIPHERTEXT",
        password=SECRET_PASSWORD,
        biometric_verified=biometric,
        **_common(confidence, cost, ref),
    )


# ── Read contour: AUTO happy paths (validate / verify / derive) within cap ─────


async def test_validate_address_auto_happy_path():
    agent, wallet, recorder = make_agent()
    outcome = await agent.validate_address(validate_intent(confidence=0.95))

    assert outcome.decision is ConfirmationDecision.AUTO
    assert outcome.executed is True
    assert outcome.result is True
    assert outcome.requires_step_up is False
    assert wallet.calls == ["validate_address:BTC:bc1qpublic"]
    assert recorder.records[0].action_taken == "VALIDATE_ADDRESS"
    assert len(recorder.records) == 1


async def test_verify_signature_auto_happy_path():
    agent, wallet, recorder = make_agent()
    outcome = await agent.verify_signature(verify_intent(confidence=0.95))

    assert outcome.decision is ConfirmationDecision.AUTO
    assert outcome.executed is True
    assert outcome.result is True
    assert wallet.calls == ["verify_signature:ETH:0xpublic"]
    assert recorder.records[0].action_taken == "VERIFY_SIGNATURE"


async def test_derive_address_auto_happy_path():
    agent, wallet, recorder = make_agent()
    outcome = await agent.derive_address(derive_intent(confidence=0.95))

    assert outcome.decision is ConfirmationDecision.AUTO
    assert outcome.executed is True
    assert isinstance(outcome.result, WalletAddress)
    assert outcome.requires_step_up is False  # reads never require step-up
    assert wallet.calls == ["derive_address:BTC:m/44'/0'/0'/0/0"]
    assert recorder.records[0].action_taken == "DERIVE_ADDRESS"


async def test_read_records_pii_compliance_contour():
    agent, _, recorder = make_agent()
    await agent.validate_address(validate_intent())
    assert any("PII" in p for p in recorder.records[0].policies_evaluated)


# ── Read contour: PII-fail → BLOCK ────────────────────────────────────────────


async def test_pii_fail_read_blocks():
    agent, wallet, recorder = make_agent()
    outcome = await agent.validate_address(
        validate_intent(), compliance_result=ComplianceResult.FAIL
    )

    assert outcome.decision is ConfirmationDecision.BLOCK
    assert outcome.executed is False
    assert outcome.halt_reason == "compliance_block"
    assert wallet.calls == []
    assert recorder.records[0].action_taken == "HALT_COMPLIANCE_BLOCK"
    assert recorder.records[0].compliance_result is ComplianceResult.FAIL


# ── Read contour: below-AUTO read → re-check halt (no HITL hold) ───────────────


async def test_below_auto_read_halts_for_recheck():
    agent, wallet, recorder = make_agent()
    outcome = await agent.validate_address(validate_intent(confidence=0.80))

    assert outcome.decision is ConfirmationDecision.REVIEW
    assert outcome.executed is False
    assert outcome.halt_reason == "review_deferred"
    assert wallet.calls == []
    assert recorder.records[0].action_taken == "HALT_REVIEW_DEFERRED"


# ── Sensitive contour: sign_tx REVIEW + biometric mandatory + HITL hold/proceed ─


async def test_sign_tx_biometric_required_halts_even_at_auto():
    # Sensitive money/key movement requires biometric step-up regardless of confidence.
    agent, wallet, recorder = make_agent()
    outcome = await agent.sign_tx(sign_intent(confidence=0.99, biometric=False))

    assert outcome.executed is False
    assert outcome.requires_step_up is True
    assert outcome.halt_reason == "step_up_required"
    assert wallet.calls == []
    assert recorder.records[0].action_taken == "HALT_STEP_UP_REQUIRED"
    assert any("biometric" in p for p in recorder.records[0].policies_evaluated)


async def test_sign_tx_review_holds_for_hitl():
    agent, wallet, recorder = make_agent()
    outcome = await agent.sign_tx(sign_intent(confidence=0.80, biometric=True))

    assert outcome.decision is ConfirmationDecision.REVIEW
    assert outcome.executed is False
    assert outcome.requires_hitl is True
    assert outcome.halt_reason == "hitl_review_required"
    assert wallet.calls == []
    assert recorder.records[0].action_taken == "HOLD_FOR_REVIEW"


async def test_sign_tx_review_proceeds_with_reviewer():
    agent, wallet, recorder = make_agent()
    outcome = await agent.sign_tx(
        sign_intent(confidence=0.80, biometric=True), human_reviewed_by="mlro-anna"
    )

    assert outcome.decision is ConfirmationDecision.REVIEW
    assert outcome.executed is True
    assert isinstance(outcome.result, SignedTx)
    assert wallet.calls == ["sign_tx:BTC:m/44'/0'/0'/0/0"]
    assert recorder.records[0].action_taken == "SIGN_TX"
    assert recorder.records[0].human_reviewed_by == "mlro-anna"


async def test_sign_tx_auto_with_biometric_executes():
    agent, wallet, recorder = make_agent()
    outcome = await agent.sign_tx(sign_intent(confidence=0.95, biometric=True))

    assert outcome.decision is ConfirmationDecision.AUTO
    assert outcome.executed is True
    assert wallet.calls == ["sign_tx:BTC:m/44'/0'/0'/0/0"]
    assert recorder.records[0].action_taken == "SIGN_TX"


# ── Sensitive contour: AML-fail → BLOCK ───────────────────────────────────────


async def test_aml_fail_sensitive_blocks():
    agent, wallet, recorder = make_agent()
    outcome = await agent.sign_tx(
        sign_intent(confidence=0.95, biometric=True),
        compliance_result=ComplianceResult.FAIL,
    )

    assert outcome.decision is ConfirmationDecision.BLOCK
    assert outcome.executed is False
    assert outcome.halt_reason == "compliance_block"
    assert wallet.calls == []
    assert recorder.records[0].action_taken == "HALT_COMPLIANCE_BLOCK"
    assert any("AML" in p for p in recorder.records[0].policies_evaluated)


# ── Sensitive contour: seed / encrypt / decrypt traverse the same REVIEW path ──


@pytest.mark.parametrize(
    ("call", "intent_factory", "expected_call"),
    [
        ("generate_seed_phrase", gen_seed_intent, "generate_seed_phrase:12"),
        ("seed_phrase_to_entropy", seed_entropy_intent, "seed_phrase_to_entropy"),
        ("encrypt", encrypt_intent, "encrypt"),
        ("decrypt", decrypt_intent, "decrypt"),
    ],
)
async def test_sensitive_ops_review_hold_then_proceed(call, intent_factory, expected_call):
    agent, wallet, recorder = make_agent()
    method = getattr(agent, call)

    hold = await method(intent_factory(confidence=0.80, biometric=True))
    assert hold.decision is ConfirmationDecision.REVIEW
    assert hold.executed is False
    assert hold.halt_reason == "hitl_review_required"
    assert wallet.calls == []

    proceed = await method(
        intent_factory(confidence=0.80, biometric=True), human_reviewed_by="ops-lead"
    )
    assert proceed.executed is True
    assert wallet.calls == [expected_call]


@pytest.mark.parametrize(
    ("call", "intent_factory"),
    [
        ("generate_seed_phrase", gen_seed_intent),
        ("seed_phrase_to_entropy", seed_entropy_intent),
        ("encrypt", encrypt_intent),
        ("decrypt", decrypt_intent),
    ],
)
async def test_sensitive_ops_biometric_mandatory(call, intent_factory):
    agent, wallet, recorder = make_agent()
    method = getattr(agent, call)
    outcome = await method(intent_factory(confidence=0.99, biometric=False))

    assert outcome.executed is False
    assert outcome.requires_step_up is True
    assert outcome.halt_reason == "step_up_required"
    assert wallet.calls == []


# ── Cost-cap breach (ADR-047): per-request AND per-window ──────────────────────


async def test_per_request_cost_cap_breach_halts():
    agent, wallet, recorder = make_agent()
    outcome = await agent.validate_address(
        validate_intent(cost=RequestCost(tokens=999_999, cost=Decimal("0.01")))
    )

    assert outcome.decision is ConfirmationDecision.BLOCK
    assert outcome.halt_reason == "cost_cap_breach"
    assert wallet.calls == []
    assert recorder.records[0].budget_breach_flag is BudgetBreach.BREACH
    assert recorder.records[0].action_taken == "HALT_COST_CAP_BREACH"


async def test_per_request_cost_cap_breach_on_amount():
    agent, wallet, _ = make_agent()
    outcome = await agent.validate_address(
        validate_intent(cost=RequestCost(tokens=10, cost=Decimal("99.00")))
    )
    assert outcome.halt_reason == "cost_cap_breach"
    assert wallet.calls == []


async def test_per_window_cost_cap_breach_halts():
    window = CostWindow(used_tokens=99_900, used_cost=Decimal("0.00"))
    agent, wallet, _ = make_agent(cost_window=window)
    outcome = await agent.validate_address(
        validate_intent(cost=RequestCost(tokens=200, cost=Decimal("0.01")))
    )

    assert outcome.decision is ConfirmationDecision.BLOCK
    assert outcome.halt_reason == "cost_cap_breach"
    assert wallet.calls == []


async def test_per_window_cost_cap_breach_on_amount():
    window = CostWindow(used_tokens=0, used_cost=Decimal("9.999"))
    agent, wallet, _ = make_agent(cost_window=window)
    outcome = await agent.validate_address(
        validate_intent(cost=RequestCost(tokens=1, cost=Decimal("0.50")))
    )
    assert outcome.halt_reason == "cost_cap_breach"
    assert wallet.calls == []


async def test_window_accumulates_only_on_execution():
    window = CostWindow()
    agent, _, _ = make_agent(cost_window=window)
    await agent.validate_address(validate_intent(cost=RequestCost(tokens=40, cost=Decimal("0.02"))))
    assert window.used_tokens == 40
    assert window.used_cost == Decimal("0.02")
    # A halted action does not accumulate.
    await agent.validate_address(validate_intent(confidence=0.50))
    assert window.used_tokens == 40


# ── Reserve-before-dispatch under concurrency (S6.3 — cost-cap TOCTOU) ─────────


class _GatedWalletPort(FakeWalletPort):
    """Wallet port whose ``validate_address`` blocks on a shared gate so every
    dispatched action is in-flight simultaneously — forcing the concurrent
    per-window race the pre-fix check-then-add (debit after this await) could not
    contain."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()

    async def validate_address(self, chain, address):
        self.calls.append(f"validate_address:{chain.value}:{address}")
        await self.gate.wait()
        return True


class _RaceLosingWindow(CostWindow):
    """Passes the pre-flight ``_cost_breaches`` read-check (``used_*`` are zero) but
    LOSES the atomic reservation — simulating a concurrent action that drained the
    budget in the gap between ``_evaluate`` and ``try_reserve``."""

    def try_reserve(self, cost: RequestCost, cap: CostCap) -> bool:
        return False


async def test_concurrent_reads_never_exceed_window_cap():
    # Window admits exactly 3 reads of 0.20 (cap 0.60); eight concurrent same-window
    # reads all clear every other gate. Reserve-before-dispatch executes exactly 3
    # and never exceeds the cap; the pre-fix check-then-add executed all 8.
    mask = make_mask(
        cost_cap=CostCap(
            max_request_tokens=10_000,
            max_request_cost=Decimal("1.00"),
            max_window_tokens=10_000_000,
            max_window_cost=Decimal("0.60"),
        )
    )
    window = CostWindow()
    wallet = _GatedWalletPort()
    agent, _, recorder = make_agent(mask=mask, wallet=wallet, cost_window=window)
    cost = RequestCost(tokens=1, cost=Decimal("0.20"))
    n = 8
    task = asyncio.gather(
        *(agent.validate_address(validate_intent(cost=cost, confidence=0.95)) for _ in range(n))
    )
    for _ in range(5):
        await asyncio.sleep(0)
    wallet.gate.set()
    outcomes = await task

    executed = [o for o in outcomes if o.executed]
    blocked = [o for o in outcomes if not o.executed]
    assert len(executed) == 3
    assert len(wallet.calls) == 3  # only reserved actions dispatched
    assert window.used_cost == Decimal("0.60")
    assert window.used_cost <= mask.cost_cap.max_window_cost
    assert all(o.halt_reason == "cost_cap_breach" for o in blocked)
    assert len(recorder.records) == n


async def test_lost_reservation_race_is_cost_cap_breach():
    # The reserve-failed branch: pre-flight check passes but the atomic reservation
    # loses the race → cost-cap BREACH, BLOCK, no port call, exactly one record.
    window = _RaceLosingWindow()
    agent, wallet, recorder = make_agent(cost_window=window)
    outcome = await agent.validate_address(validate_intent(confidence=0.95))

    assert outcome.decision is ConfirmationDecision.BLOCK
    assert outcome.executed is False
    assert outcome.halt_reason == "cost_cap_breach"
    assert wallet.calls == []
    assert len(recorder.records) == 1
    assert recorder.records[0].budget_breach_flag is BudgetBreach.BREACH
    assert recorder.records[0].action_taken == "HALT_COST_CAP_BREACH"


async def test_reservation_released_on_sensitive_port_halt():
    # Release-on-halt on the secret-bearing contour: a sign_tx that passes reserve
    # then the WalletPort raises releases its reservation — the window is unchanged,
    # so a failed signing consumes no budget (R-SEC: only the exception TYPE leaks).
    window = CostWindow()
    cost = RequestCost(tokens=300, cost=Decimal("0.02"))
    wallet = FakeWalletPort(raises=RuntimeError("HSM offline"))
    agent, _, _ = make_agent(wallet=wallet, cost_window=window)
    with pytest.raises(RuntimeError):
        await agent.sign_tx(sign_intent(confidence=0.99, biometric=True, cost=cost))
    assert window.used_tokens == 0
    assert window.used_cost == Decimal("0")


# ── BLOCK low band ────────────────────────────────────────────────────────────


async def test_block_low_confidence_read():
    agent, wallet, recorder = make_agent()
    outcome = await agent.validate_address(validate_intent(confidence=0.50))

    assert outcome.decision is ConfirmationDecision.BLOCK
    assert outcome.executed is False
    assert outcome.halt_reason == "low_confidence"
    assert wallet.calls == []
    assert recorder.records[0].action_taken == "BLOCK_LOW_CONFIDENCE"


async def test_block_low_confidence_sensitive():
    agent, wallet, recorder = make_agent()
    outcome = await agent.sign_tx(sign_intent(confidence=0.50, biometric=True))

    assert outcome.decision is ConfirmationDecision.BLOCK
    assert outcome.halt_reason == "low_confidence"
    assert wallet.calls == []


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
    outcome = await agent.validate_address(validate_intent(confidence=confidence))
    assert outcome.decision is expected


async def test_invalid_confidence_raises():
    agent, _, _ = make_agent()
    with pytest.raises(ValueError):
        await agent.validate_address(validate_intent(confidence=1.5))


# ── Unresolved process_ref (ADR-048) ──────────────────────────────────────────


async def test_unresolved_process_ref_halts():
    agent, wallet, recorder = make_agent()
    outcome = await agent.sign_tx(sign_intent(ref=ProcessRef(process_id="", version="")))

    assert outcome.decision is ConfirmationDecision.BLOCK
    assert outcome.halt_reason == "unresolved_process_ref"
    assert wallet.calls == []
    assert recorder.records[0].action_taken == "HALT_UNRESOLVED_PROCESS"


# ── Out-of-scope op rejected (ADR-049 §D3 allow-list) ─────────────────────────


async def test_out_of_scope_op_rejected():
    agent, wallet, recorder = make_agent(mask=make_mask(scope=("WalletPort.validate_address",)))
    outcome = await agent.sign_tx(sign_intent())

    assert outcome.decision is ConfirmationDecision.BLOCK
    assert outcome.halt_reason == "out_of_scope"
    assert wallet.calls == []
    assert recorder.records[0].action_taken == "REJECT_OUT_OF_SCOPE"


# ── Port error: lineage emitted, then re-raised (only the exc TYPE recorded) ───


async def test_port_error_records_then_raises():
    wallet = FakeWalletPort(raises=RuntimeError("HSM offline: seed=" + SECRET_PHRASE))
    agent, wallet, recorder = make_agent(wallet=wallet)
    with pytest.raises(RuntimeError):
        await agent.sign_tx(sign_intent(confidence=0.95, biometric=True))

    assert len(recorder.records) == 1
    rec = recorder.records[0]
    assert rec.action_taken == "HALT_WALLET_ERROR:RuntimeError"
    # R-SEC: the exception message carried a secret; only the TYPE may be recorded.
    assert SECRET_PHRASE not in rec.reasoning_summary
    assert "HSM offline" not in rec.reasoning_summary


# ── R-SEC: NO secret material in any emitted lineage record ────────────────────


def _serialize(record: AgentDecisionRecord) -> str:
    """Full string view of every field of a record (incl. cost Decimals, lists)."""
    return repr(dataclasses.asdict(record))


async def test_no_secret_material_in_lineage_records():
    """Drive every secret-bearing op (executed + halted) and assert that NO secret
    sentinel — input or returned — appears anywhere in any lineage record."""
    agent, wallet, recorder = make_agent()

    # Executed sensitive ops (AUTO + biometric): inputs AND returned secrets present.
    await agent.derive_address(derive_intent(confidence=0.95))  # seed in, address out
    await agent.generate_seed_phrase(gen_seed_intent(confidence=0.95))  # mnemonic out
    await agent.seed_phrase_to_entropy(
        seed_entropy_intent(confidence=0.95)
    )  # phrase in / entropy out
    await agent.sign_tx(sign_intent(confidence=0.95))  # seed in
    await agent.encrypt(encrypt_intent(confidence=0.95))  # plaintext + password in
    await agent.decrypt(decrypt_intent(confidence=0.95))  # password in / plaintext out
    # Halted sensitive op (no biometric): secret-bearing intent still records lineage.
    await agent.sign_tx(sign_intent(confidence=0.95, biometric=False))

    assert len(recorder.records) == 7
    for rec in recorder.records:
        blob = _serialize(rec)
        for secret in ALL_SECRETS:
            assert secret not in blob, f"secret {secret!r} leaked into {rec.action_taken}"
        # Belt-and-braces: the named secret fields are not even attribute names here.
        for f in dataclasses.fields(rec):
            val = getattr(rec, f.name)
            for secret in ALL_SECRETS:
                assert secret not in str(val)


async def test_returned_secret_on_outcome_not_in_record():
    # generate_seed_phrase returns the mnemonic on outcome.result, never on the record.
    agent, _, recorder = make_agent()
    outcome = await agent.generate_seed_phrase(gen_seed_intent(confidence=0.95))

    assert outcome.result == GENERATED_MNEMONIC  # the caller gets the secret back
    assert GENERATED_MNEMONIC not in _serialize(outcome.record)  # but the record never does


# ── Lineage obligation (ADR-046) — exactly one record per action ──────────────


async def test_exactly_one_record_per_action():
    agent, _, recorder = make_agent()
    await agent.validate_address(validate_intent())  # execute
    await agent.validate_address(validate_intent(confidence=0.50))  # halt
    await agent.sign_tx(sign_intent(confidence=0.95, biometric=True))  # execute
    assert len(recorder.records) == 3  # one per action, including the halt

    rec = recorder.records[0]
    assert rec.record_id
    assert rec.timestamp.tzinfo is not None
    assert rec.agent_id == "wallet_agent"
    assert rec.intent == "Is this a valid BTC address?"
    assert rec.correlation_id == "corr-w-1"
    assert rec.policies_evaluated  # non-empty ordered policy list
    assert 0.0 <= rec.confidence_score <= 1.0
    assert rec.cost_tokens == 50
    assert rec.cost_amount == Decimal("0.01")
    assert rec.budget_window_ref == "wallet_agent:default"
