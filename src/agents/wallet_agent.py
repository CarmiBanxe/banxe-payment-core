"""WalletAgent — L2 client-facing Wallet agent (ADR-049 Wallet mask).

WHY: ADR-049 (Intent Layer & Client-Facing Agent Masks) specifies the
client-facing **Wallet mask** (§D3 row "Wallet"): the governed surface through
which a resolved client intent becomes a bounded wallet action. This module is
the sixth and final L2 client-facing agent and mirrors the Payments mask
(``src/agents/payments_agent.py``) and FX mask (``src/agents/fx_exchange_agent.py``)
for the custody/key-material contour. It implements the agent *logic* and
*governance enforcement* of the Wallet mask; it does NOT implement the port, the
LLM-orchestration/routing layer (``AGENT_ROUTING_ENABLED`` stays out of scope —
Terminal A infra, ADR-049 §D6/§D7), or the ClickHouse sink.

The Wallet mask is **Mixed** autonomy (ADR-049 §D3): two contours share one
governance engine.

* Read contour (``derive_address``, ``verify_signature``, ``validate_address``)
  is AUTO-biased — AUTO-eligible within cap; a below-AUTO read halts (re-check),
  reads never hold for HITL. The L3 overlay here is **PII** (address/balance
  reads); a non-PASS result halts.
* Sensitive contour (``generate_seed_phrase``, ``seed_phrase_to_entropy``,
  ``sign_tx``, ``encrypt``, ``decrypt``) moves key material / value and is
  REVIEW-biased, biometric step-up is MANDATORY regardless of confidence band,
  and it holds for HITL in the REVIEW band (the Payments mutation policy). The
  L3 overlay here is **AML** for value movement.

The Wallet mask (ADR-049 §D3) enforced here, in §D2 chain order:

* ``scope``                — WalletPort operations on an allow-list; the port is
                             injected, never implemented here. An op not on the
                             allow-list is rejected.
* ``autonomy_level``       — Mixed: reads AUTO, mutations REVIEW (ADR-049 §D3).
* ``confirmation_policy``  — AUTO > 0.90 / REVIEW 0.70–0.90 / BLOCK < 0.70
                             (ADR-047 thresholds, ADR-049 §D4). A read below AUTO
                             halts (re-check); a sensitive op in the REVIEW band
                             holds for HITL and proceeds only once a human
                             reviewer is supplied (mirrors PaymentsAgent).
* ``cost_cap``             — per-request AND per-window hard caps, token AND
                             monetary (Decimal) dimensions (ADR-047 §D2, ADR-049 §D3).
* ``lineage_obligation``   — one ``AgentDecisionRecord`` per action (ADR-046), non-optional.
* ``compliance_gate``      — PII overlay for reads; AML for value movement
                             (Ruflo mandatory, L3); a non-PASS result halts.

Any one of {unresolved process_ref, out-of-scope op, below-AUTO read, REVIEW hold,
cost-cap breach, compliance fail, missing biometric step-up} halts the action
(ADR-049 §D4 — independent halt conditions). Mask *values* (caps, thresholds,
scope, gate contours) are config-as-data (CLAUDE.md §10), carried on
:class:`WalletMask`, never hardcoded in flow logic.

R-SEC (R-SEC-NEW-01, ADR-021): seed phrases, raw entropy, private keys, plaintext
passwords, and ciphertext-plaintext NEVER cross into the :class:`AgentDecisionRecord`,
the ``reasoning_summary``, or any log line. The lineage record carries only opaque
metadata (op, decision, confidence, policies, cost) and booleans. Secret material
lives solely on the intent's dedicated fields, is routed straight to the injected
WalletPort custody adapter, and — for results — is returned on
``AgentOutcome.result`` to the caller, never recorded. Mirrors the FX settlement
seed-handling discipline (``fx_exchange_agent.SettlementInstruction``). On a port
error only the exception *type* is recorded, never its message, so a misbehaving
adapter cannot leak secret material through an exception string.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from src.wallet.wallet_port import ChainId, DerivationPath, SeedMaterial, WalletPort

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
    """Mask autonomy posture (ADR-049 §D3). The Wallet mask is Mixed: the read
    contour is AUTO-biased; the sensitive contour is REVIEW-biased."""

    AUTO_BIASED = "auto_biased"
    REVIEW_BIASED = "review_biased"
    MIXED = "mixed"


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
    window_ref: str = "wallet_agent:default"

    def add(self, cost: RequestCost) -> None:
        self.used_tokens += cost.tokens
        self.used_cost += cost.cost


@dataclass(frozen=True)
class WalletMask:
    """Config-as-data Wallet mask (ADR-049 §D3). Values are governed config, not
    hardcoded flow logic; the AUTO/REVIEW/BLOCK *scale* is ADR-047 canon."""

    cost_cap: CostCap
    auto_threshold: float = 0.90
    review_floor: float = 0.70
    autonomy_level: AutonomyLevel = AutonomyLevel.MIXED
    lineage_obligation: bool = True
    # Sensitive (key-material / value-movement) ops require biometric step-up
    # regardless of confidence band (ADR-049 §D4, Payments mutation policy).
    require_step_up_for_sensitive: bool = True
    agent_id: str = "wallet_agent"

    # The mask scope (ADR-049 §D3 allow-list): the only WalletPort ops this mask
    # may reach. Reads first, then the sensitive key-material / value-movement ops.
    scope: tuple[str, ...] = (
        "WalletPort.validate_address",
        "WalletPort.verify_signature",
        "WalletPort.derive_address",
        "WalletPort.generate_seed_phrase",
        "WalletPort.seed_phrase_to_entropy",
        "WalletPort.sign_tx",
        "WalletPort.encrypt",
        "WalletPort.decrypt",
    )

    # L3 compliance contours (Ruflo mandatory): PII for address/balance reads,
    # AML for key-material / value movement (ADR-049 §D3 row "Wallet").
    compliance_gate_reads: tuple[str, ...] = ("PII",)
    compliance_gate_value_movement: tuple[str, ...] = ("AML",)


# ---------------------------------------------------------------------------
# Intents — secret material lives ONLY on these dedicated fields (R-SEC).
# It is routed straight to the injected WalletPort and is NEVER copied into the
# lineage record, the reasoning_summary, the triggering_event, or any log.
# ``intent_text`` is a non-secret human description and MUST NOT carry secrets.
# ---------------------------------------------------------------------------


@dataclass(kw_only=True)
class _WalletIntent:
    """Shared governance fields every Wallet intent carries (ADR-046/047/048)."""

    intent_text: str
    process_ref: ProcessRef
    correlation_id: str
    confidence_score: float
    request_cost: RequestCost


@dataclass(kw_only=True)
class ValidateAddressIntent(_WalletIntent):
    """Read: ``WalletPort.validate_address`` — AUTO-eligible, PII overlay, no step-up."""

    chain: ChainId
    address: str


@dataclass(kw_only=True)
class VerifySignatureIntent(_WalletIntent):
    """Read: ``WalletPort.verify_signature`` — AUTO-eligible, PII overlay, no step-up.

    ``message`` and ``signature`` are public verification inputs, not secrets."""

    chain: ChainId
    address: str
    message: str
    signature: str


@dataclass(kw_only=True)
class DeriveAddressIntent(_WalletIntent):
    """Read: ``WalletPort.derive_address`` — AUTO-eligible, PII overlay, no step-up.

    ``seed`` is SECRET key material: routed to the port, never recorded (R-SEC)."""

    seed: SeedMaterial
    chain: ChainId
    path: DerivationPath


@dataclass(kw_only=True)
class GenerateSeedPhraseIntent(_WalletIntent):
    """Sensitive: ``WalletPort.generate_seed_phrase`` — REVIEW-biased, biometric +
    HITL. The returned mnemonic is SECRET: returned on the outcome, never recorded."""

    word_count: Literal[12, 24]
    biometric_verified: bool = False


@dataclass(kw_only=True)
class SeedToEntropyIntent(_WalletIntent):
    """Sensitive: ``WalletPort.seed_phrase_to_entropy`` — REVIEW-biased, biometric +
    HITL. ``phrase`` is SECRET: routed to the port, never recorded (R-SEC)."""

    phrase: str
    biometric_verified: bool = False


@dataclass(kw_only=True)
class SignTxIntent(_WalletIntent):
    """Sensitive: ``WalletPort.sign_tx`` — REVIEW-biased, biometric + HITL.

    ``seed`` is SECRET key material: routed to the port, never recorded (R-SEC)."""

    seed: SeedMaterial
    chain: ChainId
    path: DerivationPath
    tx_payload: object = None
    biometric_verified: bool = False


@dataclass(kw_only=True)
class EncryptIntent(_WalletIntent):
    """Sensitive: ``WalletPort.encrypt`` — REVIEW-biased, biometric + HITL.

    ``plaintext`` and ``password`` are SECRET: routed to the port, never recorded."""

    plaintext: str
    password: str
    biometric_verified: bool = False


@dataclass(kw_only=True)
class DecryptIntent(_WalletIntent):
    """Sensitive: ``WalletPort.decrypt`` — REVIEW-biased, biometric + HITL.

    ``password`` is SECRET and the returned plaintext is SECRET: routed to / from
    the port, never recorded (R-SEC)."""

    ciphertext: str
    password: str
    biometric_verified: bool = False


# ---------------------------------------------------------------------------
# Lineage + outcome
# ---------------------------------------------------------------------------


@dataclass
class AgentDecisionRecord:
    """Decision-lineage record emitted per action (ADR-046 schema + ADR-047 cost).

    R-SEC: carries opaque metadata ONLY — never seed/entropy/key/password/plaintext."""

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
    compliance_contour: tuple[str, ...]
    is_sensitive: bool
    biometric_verified: bool
    human_reviewed_by: str | None
    # Sensitive ops hold for HITL in the REVIEW band; reads (AUTO-biased) instead
    # halt below AUTO. Defaults False so the read contour is unchanged.
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


class WalletAgent:
    """L2 Wallet agent enforcing the ADR-049 Wallet mask.

    The port and the lineage recorder are injected as interfaces (constructor
    injection); the agent contains pure governance logic and is unit-testable
    without any live infra. The custody crypto itself is never implemented here —
    secret material on the intents is routed straight to ``wallet_port`` and is
    never logged nor recorded (R-SEC-NEW-01).
    """

    def __init__(
        self,
        *,
        wallet_port: WalletPort,
        recorder: DecisionRecorder,
        mask: WalletMask,
        cost_window: CostWindow | None = None,
    ) -> None:
        self._wallet = wallet_port
        self._recorder = recorder
        self._mask = mask
        self._window = cost_window or CostWindow(window_ref=f"{mask.agent_id}:default")

    # -- read contour (AUTO-eligible, PII overlay, no step-up) ----------------

    async def validate_address(
        self,
        intent: ValidateAddressIntent,
        *,
        compliance_result: ComplianceResult = ComplianceResult.PASS,
    ) -> AgentOutcome:
        """Read via ``WalletPort.validate_address`` — AUTO-eligible (ADR-049 §D3)."""
        ctx = self._read_ctx(
            intent,
            triggering_event=f"validate_address:{intent.chain.value}:{intent.address}",
            success_action="VALIDATE_ADDRESS",
            op="WalletPort.validate_address",
            compliance_result=compliance_result,
        )
        return await self._run_action(
            ctx, lambda: self._wallet.validate_address(intent.chain, intent.address)
        )

    async def verify_signature(
        self,
        intent: VerifySignatureIntent,
        *,
        compliance_result: ComplianceResult = ComplianceResult.PASS,
    ) -> AgentOutcome:
        """Read via ``WalletPort.verify_signature`` — AUTO-eligible (ADR-049 §D3)."""
        ctx = self._read_ctx(
            intent,
            triggering_event=f"verify_signature:{intent.chain.value}:{intent.address}",
            success_action="VERIFY_SIGNATURE",
            op="WalletPort.verify_signature",
            compliance_result=compliance_result,
        )
        return await self._run_action(
            ctx,
            lambda: self._wallet.verify_signature(
                intent.chain, intent.address, intent.message, intent.signature
            ),
        )

    async def derive_address(
        self,
        intent: DeriveAddressIntent,
        *,
        compliance_result: ComplianceResult = ComplianceResult.PASS,
    ) -> AgentOutcome:
        """Read via ``WalletPort.derive_address`` — AUTO-eligible (ADR-049 §D3).

        ``intent.seed`` is SECRET: handed to the port, never recorded (R-SEC)."""
        ctx = self._read_ctx(
            intent,
            triggering_event=f"derive_address:{intent.chain.value}:{intent.path}",
            success_action="DERIVE_ADDRESS",
            op="WalletPort.derive_address",
            compliance_result=compliance_result,
        )
        return await self._run_action(
            ctx, lambda: self._wallet.derive_address(intent.seed, intent.chain, intent.path)
        )

    # -- sensitive contour (REVIEW-biased, AML, biometric + HITL mandatory) ---

    async def generate_seed_phrase(
        self,
        intent: GenerateSeedPhraseIntent,
        *,
        compliance_result: ComplianceResult = ComplianceResult.PASS,
        human_reviewed_by: str | None = None,
    ) -> AgentOutcome:
        """Sensitive: ``WalletPort.generate_seed_phrase`` (ADR-049 §D4 step-up).

        The returned mnemonic is SECRET: on ``outcome.result`` only, never recorded."""
        ctx = self._sensitive_ctx(
            intent,
            triggering_event=f"generate_seed_phrase:words={intent.word_count}",
            success_action="GENERATE_SEED_PHRASE",
            op="WalletPort.generate_seed_phrase",
            compliance_result=compliance_result,
            human_reviewed_by=human_reviewed_by,
        )
        return await self._run_action(
            ctx, lambda: self._wallet.generate_seed_phrase(intent.word_count)
        )

    async def seed_phrase_to_entropy(
        self,
        intent: SeedToEntropyIntent,
        *,
        compliance_result: ComplianceResult = ComplianceResult.PASS,
        human_reviewed_by: str | None = None,
    ) -> AgentOutcome:
        """Sensitive: ``WalletPort.seed_phrase_to_entropy`` (ADR-049 §D4 step-up).

        ``intent.phrase`` and the returned entropy are SECRET — never recorded."""
        ctx = self._sensitive_ctx(
            intent,
            triggering_event="seed_phrase_to_entropy",
            success_action="SEED_TO_ENTROPY",
            op="WalletPort.seed_phrase_to_entropy",
            compliance_result=compliance_result,
            human_reviewed_by=human_reviewed_by,
        )
        return await self._run_action(
            ctx, lambda: self._wallet.seed_phrase_to_entropy(intent.phrase)
        )

    async def sign_tx(
        self,
        intent: SignTxIntent,
        *,
        compliance_result: ComplianceResult = ComplianceResult.PASS,
        human_reviewed_by: str | None = None,
    ) -> AgentOutcome:
        """Sensitive: ``WalletPort.sign_tx`` (ADR-049 §D4 step-up mandatory).

        ``intent.seed`` is SECRET key material: handed to the port, never recorded."""
        ctx = self._sensitive_ctx(
            intent,
            triggering_event=f"sign_tx:{intent.chain.value}:{intent.path}",
            success_action="SIGN_TX",
            op="WalletPort.sign_tx",
            compliance_result=compliance_result,
            human_reviewed_by=human_reviewed_by,
        )
        return await self._run_action(
            ctx,
            lambda: self._wallet.sign_tx(intent.seed, intent.chain, intent.path, intent.tx_payload),
        )

    async def encrypt(
        self,
        intent: EncryptIntent,
        *,
        compliance_result: ComplianceResult = ComplianceResult.PASS,
        human_reviewed_by: str | None = None,
    ) -> AgentOutcome:
        """Sensitive: ``WalletPort.encrypt`` (ADR-049 §D4 step-up mandatory).

        ``intent.plaintext`` and ``intent.password`` are SECRET — never recorded."""
        ctx = self._sensitive_ctx(
            intent,
            triggering_event="encrypt",
            success_action="ENCRYPT",
            op="WalletPort.encrypt",
            compliance_result=compliance_result,
            human_reviewed_by=human_reviewed_by,
        )
        return await self._run_action(
            ctx, lambda: self._wallet.encrypt(intent.plaintext, intent.password)
        )

    async def decrypt(
        self,
        intent: DecryptIntent,
        *,
        compliance_result: ComplianceResult = ComplianceResult.PASS,
        human_reviewed_by: str | None = None,
    ) -> AgentOutcome:
        """Sensitive: ``WalletPort.decrypt`` (ADR-049 §D4 step-up mandatory).

        ``intent.password`` and the returned plaintext are SECRET — never recorded."""
        ctx = self._sensitive_ctx(
            intent,
            triggering_event="decrypt",
            success_action="DECRYPT",
            op="WalletPort.decrypt",
            compliance_result=compliance_result,
            human_reviewed_by=human_reviewed_by,
        )
        return await self._run_action(
            ctx, lambda: self._wallet.decrypt(intent.ciphertext, intent.password)
        )

    # -- context builders ----------------------------------------------------

    def _read_ctx(
        self,
        intent: _WalletIntent,
        *,
        triggering_event: str,
        success_action: str,
        op: str,
        compliance_result: ComplianceResult,
    ) -> _ActionContext:
        return _ActionContext(
            intent_text=intent.intent_text,
            process_ref=intent.process_ref,
            correlation_id=intent.correlation_id,
            confidence_score=intent.confidence_score,
            triggering_event=triggering_event,
            success_action=success_action,
            op=op,
            request_cost=intent.request_cost,
            compliance_result=compliance_result,
            compliance_contour=self._mask.compliance_gate_reads,
            is_sensitive=False,
            biometric_verified=False,
            human_reviewed_by=None,
            supports_review_hitl=False,
        )

    def _sensitive_ctx(
        self,
        intent: GenerateSeedPhraseIntent
        | SeedToEntropyIntent
        | SignTxIntent
        | EncryptIntent
        | DecryptIntent,
        *,
        triggering_event: str,
        success_action: str,
        op: str,
        compliance_result: ComplianceResult,
        human_reviewed_by: str | None,
    ) -> _ActionContext:
        return _ActionContext(
            intent_text=intent.intent_text,
            process_ref=intent.process_ref,
            correlation_id=intent.correlation_id,
            confidence_score=intent.confidence_score,
            triggering_event=triggering_event,
            success_action=success_action,
            op=op,
            request_cost=intent.request_cost,
            compliance_result=compliance_result,
            compliance_contour=self._mask.compliance_gate_value_movement,
            is_sensitive=True,
            biometric_verified=intent.biometric_verified,
            human_reviewed_by=human_reviewed_by,
            supports_review_hitl=True,
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
        # Reads are never sensitive, so this is always False for the read contour.
        # Sensitive ops are step-up-mandatory regardless of confidence band.
        return ctx.is_sensitive and self._mask.require_step_up_for_sensitive

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
                f"Operation {ctx.op} is not on the Wallet mask scope allow-list; refused.",
                policies,
                ComplianceResult.NA,
                BudgetBreach.NONE,
                halt_reason="out_of_scope",
            )

        # 3. ADR-047 confidence band (AUTO > 0.90 / REVIEW 0.70–0.90 / BLOCK < 0.70).
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
            # Read contour: reads are AUTO-only under the Mixed mask, so a below-AUTO
            # read halts (re-check at higher confidence), not a HITL hold (ADR-049 §D3).
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
            # Sensitive contour: REVIEW band holds for HITL and proceeds only once a
            # human reviewer is supplied (Payments mutation policy, ADR-049 §D4).
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

        # 5. L3 compliance gate (PII for reads / AML for value movement; Ruflo mandatory).
        policies.append("ADR-049-compliance-gate:" + "+".join(ctx.compliance_contour))
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

        # 6. ADR-049 §D4 — biometric step-up for key-material / value movement,
        #    mandatory regardless of confidence band. The read contour is never
        #    sensitive, so this gate trips only for the sensitive ops.
        if self._step_up_required(ctx) and not ctx.biometric_verified:
            policies.append("ADR-049-D4-biometric-step-up")
            return _Evaluation(
                band,
                False,
                "HALT_STEP_UP_REQUIRED",
                "Sensitive key-material/value movement requires biometric step-up (ADR-049 §D4).",
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
        self, ctx: _ActionContext, port_call: Callable[[], Awaitable[object]]
    ) -> AgentOutcome:
        ev = self._evaluate(ctx)
        result: object | None = None
        executed = False
        action_taken = ev.action_taken

        if ev.proceed:
            try:
                result = await port_call()
            except Exception as exc:  # noqa: BLE001 — lineage emitted before re-raise
                # R-SEC: record only the exception TYPE, never its message, so a
                # misbehaving adapter cannot leak secret material via the record.
                action_taken = f"HALT_WALLET_ERROR:{type(exc).__name__}"
                await self._emit(
                    ctx,
                    ev,
                    action_taken,
                    executed=False,
                    reasoning=f"WalletPort raised {type(exc).__name__}; action not completed.",
                )
                raise
            executed = True
            self._window.add(ctx.request_cost)

        record = await self._emit(
            ctx,
            ev,
            action_taken,
            executed=executed,
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
        reasoning: str,
    ) -> AgentDecisionRecord:
        # R-SEC: every field below is opaque metadata. No intent secret field
        # (seed/entropy/phrase/password/plaintext/ciphertext) is ever read here.
        record = AgentDecisionRecord(
            record_id=str(uuid.uuid4()),
            timestamp=datetime.now(UTC),
            agent_id=self._mask.agent_id,
            triggering_event=ctx.triggering_event,
            intent=ctx.intent_text,
            policies_evaluated=ev.policies,
            compliance_result=ev.compliance_result,
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
    "DecryptIntent",
    "DeriveAddressIntent",
    "EncryptIntent",
    "GenerateSeedPhraseIntent",
    "ProcessRef",
    "RequestCost",
    "SeedToEntropyIntent",
    "SignTxIntent",
    "ValidateAddressIntent",
    "VerifySignatureIntent",
    "WalletAgent",
    "WalletMask",
]
