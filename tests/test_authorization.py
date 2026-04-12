"""Tests for AuthEngine — compliance + balance → approve/decline."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.authorization.auth_engine import (
    _HITL_THRESHOLD_MINOR,
    _RC_APPROVE,
    _RC_DECLINE,
    _RC_INSUFFICIENT,
    _RC_REFER,
    AuthEngine,
)
from src.compliance_bridge.screening import (
    ComplianceScreening,
    ScreeningDecision,
    ScreeningResult,
)
from src.ports.issuer_port import AuthorisationDecision, AuthorisationRequest
from src.ports.ledger_port import BalanceResult


def make_request(amount_minor: int = 1000) -> AuthorisationRequest:
    return AuthorisationRequest(
        card_id="card_001",
        amount_minor=amount_minor,
        currency="GBP",
        merchant_name="TESCO",
        merchant_category_code="5411",
        transaction_id="txn_001",
        idempotency_key="idem_001",
    )


def make_engine(
    screening_decision: ScreeningDecision = ScreeningDecision.PASS,
    available_minor: int = 100_000,
) -> AuthEngine:
    ledger = MagicMock()
    ledger.get_balance = AsyncMock(
        return_value=BalanceResult(
            account_id="card_001",
            available_minor=available_minor,
            pending_minor=0,
            currency="GBP",
        )
    )
    screening = MagicMock(spec=ComplianceScreening)
    screening.screen = AsyncMock(
        return_value=ScreeningResult(decision=screening_decision, risk_score=10)
    )
    return AuthEngine(ledger=ledger, screening=screening)


class TestAuthEngineApprove:
    @pytest.mark.asyncio
    async def test_approve_normal_transaction(self):
        engine = make_engine()
        result = await engine.authorise(make_request(amount_minor=1000))
        assert result.decision == AuthorisationDecision.APPROVE
        assert result.response_code == _RC_APPROVE

    @pytest.mark.asyncio
    async def test_approved_available_balance_updated(self):
        engine = make_engine(available_minor=50_000)
        result = await engine.authorise(make_request(amount_minor=10_000))
        assert result.available_balance_minor == 40_000


class TestAuthEngineDecline:
    @pytest.mark.asyncio
    async def test_block_on_compliance_screening(self):
        engine = make_engine(screening_decision=ScreeningDecision.BLOCK)
        result = await engine.authorise(make_request())
        assert result.decision == AuthorisationDecision.DECLINE
        assert result.response_code == _RC_DECLINE

    @pytest.mark.asyncio
    async def test_decline_insufficient_funds(self):
        engine = make_engine(available_minor=500)
        result = await engine.authorise(make_request(amount_minor=1000))
        assert result.decision == AuthorisationDecision.DECLINE
        assert result.response_code == _RC_INSUFFICIENT

    @pytest.mark.asyncio
    async def test_decline_zero_balance(self):
        engine = make_engine(available_minor=0)
        result = await engine.authorise(make_request(amount_minor=1))
        assert result.decision == AuthorisationDecision.DECLINE


class TestAuthEngineRefer:
    @pytest.mark.asyncio
    async def test_refer_on_compliance_review(self):
        engine = make_engine(screening_decision=ScreeningDecision.REVIEW)
        result = await engine.authorise(make_request())
        assert result.decision == AuthorisationDecision.REFER
        assert result.response_code == _RC_REFER

    @pytest.mark.asyncio
    async def test_refer_on_hitl_threshold(self):
        """I-04: amounts ≥ £10,000 must trigger HITL."""
        engine = make_engine(available_minor=5_000_000)  # 50k available
        result = await engine.authorise(make_request(amount_minor=_HITL_THRESHOLD_MINOR))
        assert result.decision == AuthorisationDecision.REFER
        assert result.response_code == _RC_REFER

    @pytest.mark.asyncio
    async def test_below_hitl_threshold_approved(self):
        """Just below £10,000 threshold — should approve if funds available."""
        engine = make_engine(available_minor=5_000_000)
        result = await engine.authorise(make_request(amount_minor=_HITL_THRESHOLD_MINOR - 1))
        assert result.decision == AuthorisationDecision.APPROVE


class TestAuthEngineInvariants:
    @pytest.mark.asyncio
    async def test_compliance_checked_before_balance(self):
        """I-01: compliance must be checked before balance query."""
        ledger = MagicMock()
        ledger.get_balance = AsyncMock()
        screening = MagicMock(spec=ComplianceScreening)
        screening.screen = AsyncMock(return_value=ScreeningResult(decision=ScreeningDecision.BLOCK))
        engine = AuthEngine(ledger=ledger, screening=screening)
        await engine.authorise(make_request())
        ledger.get_balance.assert_not_called()  # balance NOT checked when blocked
