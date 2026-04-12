"""Tests for adapter constructors and I-10 enforcement.

Adapters require external services (Sprint 9/10) — we test:
- NotImplementedError raised when env vars not configured (I-10)
- Correct port inheritance (ABC contract)
"""

import pytest

from src.adapters.hyperswitch_adapter import HyperswitchAdapter
from src.adapters.midaz_adapter import MidazAdapter
from src.adapters.paymentology_adapter import PaymentologyAdapter
from src.ports.issuer_port import IssuerPort
from src.ports.ledger_port import LedgerPort
from src.ports.payment_switch_port import PaymentSwitchPort


class TestHyperswitchAdapter:
    def test_raises_not_implemented_without_url(self, monkeypatch):
        """I-10: no fake integrations — must raise if URL not set."""
        monkeypatch.delenv("HYPERSWITCH_BASE_URL", raising=False)
        with pytest.raises(NotImplementedError, match="HYPERSWITCH_BASE_URL"):
            HyperswitchAdapter(base_url="")

    def test_instantiates_with_url(self):
        """Valid URL → adapter created without error."""
        adapter = HyperswitchAdapter(base_url="http://localhost:8096")
        assert isinstance(adapter, PaymentSwitchPort)

    def test_inherits_payment_switch_port(self):
        adapter = HyperswitchAdapter(base_url="http://localhost:8096")
        assert isinstance(adapter, PaymentSwitchPort)

    def test_api_key_from_kwarg(self):
        adapter = HyperswitchAdapter(base_url="http://localhost:8096", api_key="test_key")
        assert adapter._api_key == "test_key"

    def test_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("HYPERSWITCH_BASE_URL", "http://localhost:8096")
        monkeypatch.setenv("HYPERSWITCH_API_KEY", "env_key")
        adapter = HyperswitchAdapter()
        assert adapter._api_key == "env_key"

    def test_base_url_trailing_slash_stripped(self):
        adapter = HyperswitchAdapter(base_url="http://localhost:8096/")
        assert not adapter._base_url.endswith("/")

    def test_parse_result_authorized(self):
        result = HyperswitchAdapter._parse_result(
            {
                "status": "requires_capture",
                "payment_id": "pay_001",
                "amount": 1000,
                "currency": "GBP",
            }
        )
        from src.ports.payment_switch_port import PaymentStatus

        assert result.status == PaymentStatus.AUTHORIZED
        assert result.transaction_id == "pay_001"
        assert result.amount_minor == 1000

    def test_parse_result_unknown_status_falls_back_to_failed(self):
        from src.ports.payment_switch_port import PaymentStatus

        result = HyperswitchAdapter._parse_result({"status": "unknown_status"})
        assert result.status == PaymentStatus.FAILED


class TestPaymentologyAdapter:
    def test_raises_not_implemented_without_url(self, monkeypatch):
        """I-10: no fake integrations."""
        monkeypatch.delenv("PAYMENTOLOGY_BASE_URL", raising=False)
        with pytest.raises(NotImplementedError, match="PAYMENTOLOGY_BASE_URL"):
            PaymentologyAdapter(base_url="")

    def test_instantiates_with_url(self):
        adapter = PaymentologyAdapter(base_url="https://api.paymentology.com")
        assert isinstance(adapter, IssuerPort)

    def test_inherits_issuer_port(self):
        adapter = PaymentologyAdapter(base_url="https://api.paymentology.com")
        assert isinstance(adapter, IssuerPort)

    def test_authorise_transaction_is_stub(self):
        """Companion API authorise_transaction requires auth_engine wiring (Sprint 10)."""
        adapter = PaymentologyAdapter(base_url="https://api.paymentology.com")
        import asyncio

        from src.ports.issuer_port import AuthorisationRequest

        req = AuthorisationRequest(
            card_id="card_001",
            amount_minor=1000,
            currency="GBP",
            merchant_name="TESCO",
            merchant_category_code="5411",
            transaction_id="txn_001",
            idempotency_key="idem_001",
        )
        with pytest.raises(NotImplementedError, match="auth_engine"):
            asyncio.get_event_loop().run_until_complete(adapter.authorise_transaction(req))


class TestMidazAdapter:
    def test_raises_not_implemented_without_org(self, monkeypatch):
        """I-10: no fake integrations — must raise if org/ledger not set."""
        monkeypatch.delenv("MIDAZ_ORG_ID", raising=False)
        monkeypatch.delenv("MIDAZ_LEDGER_ID", raising=False)
        with pytest.raises(NotImplementedError, match="MIDAZ_ORG_ID"):
            MidazAdapter(org_id="", ledger_id="")

    def test_instantiates_with_config(self):
        adapter = MidazAdapter(org_id="org_001", ledger_id="ledger_001")
        assert isinstance(adapter, LedgerPort)

    def test_inherits_ledger_port(self):
        adapter = MidazAdapter(org_id="org_001", ledger_id="ledger_001")
        assert isinstance(adapter, LedgerPort)

    def test_default_base_url(self):
        adapter = MidazAdapter(org_id="org_001", ledger_id="ledger_001")
        assert "8095" in adapter._base_url

    def test_base_url_override(self):
        adapter = MidazAdapter(
            base_url="http://prod-midaz:8095",
            org_id="org_001",
            ledger_id="ledger_001",
        )
        assert "prod-midaz" in adapter._base_url
