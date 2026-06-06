"""Tests for adapter constructors and I-10 enforcement.

Adapters require external services (Sprint 9/10) — we test:
- NotImplementedError raised when env vars not configured (I-10)
- Correct port inheritance (ABC contract)
- HTTP method behaviour via mocked httpx.AsyncClient
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.adapters.hyperswitch_adapter import HyperswitchAdapter
from src.adapters.midaz_adapter import MidazAdapter
from src.adapters.paymentology_adapter import PaymentologyAdapter
from src.ports.issuer_port import IssuerPort
from src.ports.ledger_port import LedgerEntry, LedgerPort
from src.ports.payment_switch_port import PaymentRequest, PaymentStatus, PaymentSwitchPort

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_async_client_mock(response_data: dict, *, status_code: int = 200):
    """Return a mock httpx.AsyncClient context manager with preset response."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = response_data
    mock_resp.raise_for_status.return_value = None
    mock_resp.status_code = status_code

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post.return_value = mock_resp
    mock_client.get.return_value = mock_resp
    mock_client.patch.return_value = mock_resp
    return mock_client


def _make_payment_request(**kwargs) -> PaymentRequest:
    defaults = dict(
        amount_minor=1000,
        currency="GBP",
        card_token="tok_test",
        customer_id="cust_001",
        merchant_id="merch_001",
        idempotency_key="idem_001",
    )
    defaults.update(kwargs)
    return PaymentRequest(**defaults)


def _make_ledger_entry(**kwargs) -> LedgerEntry:
    defaults = dict(
        account_id="acc_001",
        amount_minor=500,
        currency="GBP",
        reference="ref_001",
        idempotency_key="idem_001",
    )
    defaults.update(kwargs)
    return LedgerEntry(**defaults)


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


# ---------------------------------------------------------------------------
# HyperswitchAdapter — HTTP methods (mocked)
# ---------------------------------------------------------------------------

class TestHyperswitchAdapterHTTP:
    def _adapter(self):
        return HyperswitchAdapter(base_url="http://localhost:8096", api_key="test_key")

    @pytest.mark.asyncio
    async def test_authorize_returns_result(self):
        adapter = self._adapter()
        mock_client = _make_async_client_mock(
            {"status": "requires_capture", "payment_id": "pay_001", "amount": 1000, "currency": "GBP"}
        )
        with patch("src.adapters.hyperswitch_adapter.httpx.AsyncClient", return_value=mock_client):
            req = _make_payment_request()
            result = await adapter.authorize(req)
        assert result.status == PaymentStatus.AUTHORIZED
        assert result.transaction_id == "pay_001"
        assert result.amount_minor == 1000

    @pytest.mark.asyncio
    async def test_capture_returns_result(self):
        adapter = self._adapter()
        mock_client = _make_async_client_mock(
            {"status": "succeeded", "payment_id": "pay_002", "amount": 1000, "currency": "GBP"}
        )
        with patch("src.adapters.hyperswitch_adapter.httpx.AsyncClient", return_value=mock_client):
            result = await adapter.capture("pay_002", amount_minor=1000)
        assert result.status == PaymentStatus.CAPTURED
        assert result.transaction_id == "pay_002"

    @pytest.mark.asyncio
    async def test_capture_without_amount(self):
        adapter = self._adapter()
        mock_client = _make_async_client_mock(
            {"status": "succeeded", "payment_id": "pay_003", "amount": 500, "currency": "EUR"}
        )
        with patch("src.adapters.hyperswitch_adapter.httpx.AsyncClient", return_value=mock_client):
            result = await adapter.capture("pay_003")
        assert result.status == PaymentStatus.CAPTURED

    @pytest.mark.asyncio
    async def test_refund_returns_refunded_status(self):
        adapter = self._adapter()
        mock_client = _make_async_client_mock(
            {"refund_id": "ref_001", "connector_refund_id": "cnx_001", "amount": 500, "currency": "GBP"}
        )
        with patch("src.adapters.hyperswitch_adapter.httpx.AsyncClient", return_value=mock_client):
            result = await adapter.refund("pay_001", amount_minor=500, reason="customer_request")
        assert result.status == PaymentStatus.REFUNDED
        assert result.transaction_id == "ref_001"
        assert result.amount_minor == 500

    @pytest.mark.asyncio
    async def test_refund_fallback_transaction_id(self):
        adapter = self._adapter()
        mock_client = _make_async_client_mock({"amount": 200, "currency": "GBP"})
        with patch("src.adapters.hyperswitch_adapter.httpx.AsyncClient", return_value=mock_client):
            result = await adapter.refund("pay_x", amount_minor=200, reason="duplicate")
        assert result.transaction_id == "pay_x"

    @pytest.mark.asyncio
    async def test_void_returns_voided_status(self):
        adapter = self._adapter()
        mock_client = _make_async_client_mock(
            {"status": "cancelled", "payment_id": "pay_004", "amount": 0, "currency": "GBP"}
        )
        with patch("src.adapters.hyperswitch_adapter.httpx.AsyncClient", return_value=mock_client):
            result = await adapter.void("pay_004")
        assert result.status == PaymentStatus.VOIDED

    @pytest.mark.asyncio
    async def test_get_payment_status(self):
        adapter = self._adapter()
        mock_client = _make_async_client_mock(
            {"status": "succeeded", "payment_id": "pay_005", "amount": 750, "currency": "USD"}
        )
        with patch("src.adapters.hyperswitch_adapter.httpx.AsyncClient", return_value=mock_client):
            result = await adapter.get_payment_status("pay_005")
        assert result.status == PaymentStatus.CAPTURED
        assert result.currency == "USD"

    def test_headers_include_api_key(self):
        adapter = self._adapter()
        headers = adapter._headers
        assert headers["api-key"] == "test_key"
        assert headers["Content-Type"] == "application/json"


# ---------------------------------------------------------------------------
# MidazAdapter — HTTP methods (mocked)
# ---------------------------------------------------------------------------

class TestMidazAdapterHTTP:
    def _adapter(self):
        return MidazAdapter(
            base_url="http://localhost:8095",
            api_key="midaz_key",
            org_id="org_001",
            ledger_id="ledger_001",
        )

    @pytest.mark.asyncio
    async def test_get_balance_returns_balance_result(self):
        adapter = self._adapter()
        mock_client = _make_async_client_mock(
            {"balance": {"available": 50000, "onHold": 1000}}
        )
        with patch("src.adapters.midaz_adapter.httpx.AsyncClient", return_value=mock_client):
            result = await adapter.get_balance("acc_001", "GBP")
        assert result.available_minor == 50000
        assert result.pending_minor == 1000
        assert result.currency == "GBP"

    @pytest.mark.asyncio
    async def test_get_balance_empty_balance_field(self):
        adapter = self._adapter()
        mock_client = _make_async_client_mock({})
        with patch("src.adapters.midaz_adapter.httpx.AsyncClient", return_value=mock_client):
            result = await adapter.get_balance("acc_002", "EUR")
        assert result.available_minor == 0
        assert result.pending_minor == 0

    @pytest.mark.asyncio
    async def test_debit_returns_transaction_id(self):
        adapter = self._adapter()
        mock_client = _make_async_client_mock({"id": "txn_debit_001"})
        with patch("src.adapters.midaz_adapter.httpx.AsyncClient", return_value=mock_client):
            entry = _make_ledger_entry(amount_minor=-500)
            txn_id = await adapter.debit(entry)
        assert txn_id == "txn_debit_001"

    @pytest.mark.asyncio
    async def test_credit_returns_transaction_id(self):
        adapter = self._adapter()
        mock_client = _make_async_client_mock({"id": "txn_credit_001"})
        with patch("src.adapters.midaz_adapter.httpx.AsyncClient", return_value=mock_client):
            entry = _make_ledger_entry(amount_minor=1000)
            txn_id = await adapter.credit(entry)
        assert txn_id == "txn_credit_001"

    @pytest.mark.asyncio
    async def test_reserve_returns_transaction_id(self):
        adapter = self._adapter()
        mock_client = _make_async_client_mock({"id": "txn_reserve_001"})
        with patch("src.adapters.midaz_adapter.httpx.AsyncClient", return_value=mock_client):
            entry = _make_ledger_entry(amount_minor=200)
            txn_id = await adapter.reserve(entry)
        assert txn_id == "txn_reserve_001"

    @pytest.mark.asyncio
    async def test_release_reservation(self):
        adapter = self._adapter()
        mock_client = _make_async_client_mock({})
        with patch("src.adapters.midaz_adapter.httpx.AsyncClient", return_value=mock_client):
            await adapter.release_reservation("res_001")
        mock_client.patch.assert_called_once()

    @pytest.mark.asyncio
    async def test_settle_reservation_with_amount(self):
        adapter = self._adapter()
        mock_client = _make_async_client_mock({"id": "txn_settled_001"})
        with patch("src.adapters.midaz_adapter.httpx.AsyncClient", return_value=mock_client):
            result = await adapter.settle_reservation("res_001", actual_amount_minor=800)
        assert result == "txn_settled_001"

    @pytest.mark.asyncio
    async def test_settle_reservation_without_amount(self):
        adapter = self._adapter()
        mock_client = _make_async_client_mock({"id": "txn_settled_002"})
        with patch("src.adapters.midaz_adapter.httpx.AsyncClient", return_value=mock_client):
            result = await adapter.settle_reservation("res_002")
        assert result == "txn_settled_002"

    def test_accounts_url_format(self):
        adapter = self._adapter()
        url = adapter._accounts_url("acc_test")
        assert "/v1/organizations/org_001/ledgers/ledger_001/accounts/acc_test" in url

    def test_transactions_url_format(self):
        adapter = self._adapter()
        url = adapter._transactions_url()
        assert "/v1/organizations/org_001/ledgers/ledger_001/transactions" in url

    def test_headers_include_bearer(self):
        adapter = self._adapter()
        assert adapter._headers["Authorization"] == "Bearer midaz_key"


# ---------------------------------------------------------------------------
# PaymentologyAdapter — HTTP methods (mocked)
# ---------------------------------------------------------------------------

class TestPaymentologyAdapterHTTP:
    def _adapter(self):
        return PaymentologyAdapter(
            base_url="https://api.paymentology.com",
            api_key="pay_key",
        )

    @pytest.mark.asyncio
    async def test_issue_card_returns_card_result(self):
        from src.ports.issuer_port import CardIssueRequest, CardStatus
        adapter = self._adapter()
        mock_client = _make_async_client_mock({
            "cardId": "card_001",
            "maskedPan": "****1234",
            "status": "ACTIVE",
            "expiryMonth": 12,
            "expiryYear": 2028,
        })
        with patch("src.adapters.paymentology_adapter.httpx.AsyncClient", return_value=mock_client):
            req = CardIssueRequest(
                customer_id="cust_001",
                program_id="prog_001",
                cardholder_name="John Doe",
                currency="GBP",
            )
            result = await adapter.issue_card(req)
        assert result.card_id == "card_001"
        assert result.masked_pan == "****1234"
        assert result.status == CardStatus.ACTIVE
        assert result.expiry_year == 2028

    @pytest.mark.asyncio
    async def test_suspend_card_returns_suspended_status(self):
        from src.ports.issuer_port import CardStatus
        adapter = self._adapter()
        mock_client = _make_async_client_mock({})
        with patch("src.adapters.paymentology_adapter.httpx.AsyncClient", return_value=mock_client):
            status = await adapter.suspend_card("card_001", reason="fraud")
        assert status == CardStatus.SUSPENDED

    @pytest.mark.asyncio
    async def test_cancel_card_returns_cancelled_status(self):
        from src.ports.issuer_port import CardStatus
        adapter = self._adapter()
        mock_client = _make_async_client_mock({})
        with patch("src.adapters.paymentology_adapter.httpx.AsyncClient", return_value=mock_client):
            status = await adapter.cancel_card("card_002", reason="customer_request")
        assert status == CardStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_get_card_status_active(self):
        from src.ports.issuer_port import CardStatus
        adapter = self._adapter()
        mock_client = _make_async_client_mock({"status": "ACTIVE"})
        with patch("src.adapters.paymentology_adapter.httpx.AsyncClient", return_value=mock_client):
            status = await adapter.get_card_status("card_003")
        assert status == CardStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_get_card_status_unknown_defaults_pending(self):
        from src.ports.issuer_port import CardStatus
        adapter = self._adapter()
        mock_client = _make_async_client_mock({"status": "UNKNOWN_STATUS"})
        with patch("src.adapters.paymentology_adapter.httpx.AsyncClient", return_value=mock_client):
            status = await adapter.get_card_status("card_004")
        assert status == CardStatus.PENDING
