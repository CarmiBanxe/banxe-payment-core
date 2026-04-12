"""Tests for port ABC contracts — verifies all ports are properly abstract."""

import pytest

from src.ports.issuer_port import (
    AuthorisationDecision,
    AuthorisationRequest,
    IssuerPort,
)
from src.ports.ledger_port import BalanceResult, LedgerEntry, LedgerPort
from src.ports.payment_switch_port import (
    PaymentRequest,
    PaymentResult,
    PaymentStatus,
    PaymentSwitchPort,
)

# ── PaymentSwitchPort ─────────────────────────────────────────────────────────


class TestPaymentSwitchPort:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            PaymentSwitchPort()  # type: ignore[abstract]

    def test_payment_request_dataclass(self):
        req = PaymentRequest(
            amount_minor=1000,
            currency="GBP",
            customer_id="cust_001",
            card_token="tok_abc123",
            merchant_id="merch_001",
            idempotency_key="idem_001",
        )
        assert req.amount_minor == 1000
        assert req.currency == "GBP"
        assert req.metadata is None

    def test_payment_result_dataclass(self):
        result = PaymentResult(
            status=PaymentStatus.AUTHORIZED,
            transaction_id="txn_001",
            processor_response_code="00",
            amount_minor=1000,
            currency="GBP",
        )
        assert result.status == PaymentStatus.AUTHORIZED
        assert result.error_message is None

    def test_payment_status_values(self):
        assert PaymentStatus.AUTHORIZED.value == "authorized"
        assert PaymentStatus.DECLINED.value == "declined"
        assert PaymentStatus.VOIDED.value == "voided"

    def test_amount_minor_is_int(self):
        """I-05: amounts must be int, never float."""
        req = PaymentRequest(
            amount_minor=9999,
            currency="GBP",
            customer_id="c",
            card_token="t",
            merchant_id="m",
            idempotency_key="k",
        )
        assert isinstance(req.amount_minor, int)


# ── IssuerPort ────────────────────────────────────────────────────────────────


class TestIssuerPort:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            IssuerPort()  # type: ignore[abstract]

    def test_authorisation_decision_values(self):
        assert AuthorisationDecision.APPROVE.value == "APPROVE"
        assert AuthorisationDecision.DECLINE.value == "DECLINE"
        assert AuthorisationDecision.REFER.value == "REFER"

    def test_authorisation_request_dataclass(self):
        req = AuthorisationRequest(
            card_id="card_001",
            amount_minor=5000,
            currency="GBP",
            merchant_name="TESCO",
            merchant_category_code="5411",
            transaction_id="txn_002",
            idempotency_key="idem_002",
        )
        assert req.amount_minor == 5000
        assert isinstance(req.amount_minor, int)  # I-05


# ── LedgerPort ────────────────────────────────────────────────────────────────


class TestLedgerPort:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            LedgerPort()  # type: ignore[abstract]

    def test_ledger_entry_dataclass(self):
        entry = LedgerEntry(
            account_id="acc_001",
            amount_minor=-1000,
            currency="GBP",
            reference="txn_001",
            idempotency_key="idem_001",
        )
        assert entry.amount_minor == -1000
        assert isinstance(entry.amount_minor, int)  # I-05

    def test_balance_result_dataclass(self):
        bal = BalanceResult(
            account_id="acc_001",
            available_minor=50000,
            pending_minor=5000,
            currency="GBP",
        )
        assert bal.available_minor == 50000
        assert isinstance(bal.available_minor, int)  # I-05
