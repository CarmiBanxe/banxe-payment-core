"""L2 client-facing agents (ADR-049 Intent-First Execution layer).

Public surface for the Payments and Wallet mask agents. Live LLM routing / agent
dispatch (``AGENT_ROUTING_ENABLED``) is out of scope here — Terminal A infra
(ADR-049 §D6/§D7). Each mask module defines its own intent vocabulary and
governance dataclasses (``ProcessRef``, ``CostCap``, ``AgentDecisionRecord``, …)
so it stays self-contained; the names below re-export the shared Payments surface
plus the Wallet-specific intents.
"""

from src.agents.payments_agent import (
    AddressValidationIntent,
    AgentDecisionRecord,
    AgentOutcome,
    BudgetBreach,
    ComplianceResult,
    ConfirmationDecision,
    CostCap,
    CostWindow,
    DecisionRecorder,
    PaymentIntent,
    PaymentsAgent,
    PaymentsMask,
    ProcessRef,
    RequestCost,
)
from src.agents.wallet_agent import (
    DecryptIntent,
    DeriveAddressIntent,
    EncryptIntent,
    GenerateSeedPhraseIntent,
    SeedToEntropyIntent,
    SignTxIntent,
    ValidateAddressIntent,
    VerifySignatureIntent,
    WalletAgent,
    WalletMask,
)

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
    "DecryptIntent",
    "DeriveAddressIntent",
    "EncryptIntent",
    "GenerateSeedPhraseIntent",
    "PaymentIntent",
    "PaymentsAgent",
    "PaymentsMask",
    "ProcessRef",
    "RequestCost",
    "SeedToEntropyIntent",
    "SignTxIntent",
    "ValidateAddressIntent",
    "VerifySignatureIntent",
    "WalletAgent",
    "WalletMask",
]
