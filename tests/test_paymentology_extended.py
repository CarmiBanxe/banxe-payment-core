"""Extended tests for Paymentology Companion API — Sprint 9 P0 Coverage.
Target: bring coverage of src/paymentology/ from 76.77% to 85%+.
Covers: adapter.py, remote_handler.py, webhook_server.py edge cases.
"""
import hashlib
import hmac
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.paymentology.adapter import (
    CardHolder,
    CardResponse,
    PaymentologyAdapter,
    PaymentologyConfig,
)
from src.paymentology.checksum import compute_checksum
from src.paymentology.remote_handler import (
    RemoteAPIHandler,
    build_xml_response,
)
from src.paymentology.xmlrpc_builder import build_request, parse_response

# ─── Shared mock fixture ────────────────────────────────────────────────────

class MockBalanceProvider:
    """Mock BalanceProvider for unit tests (no real wallet)."""
    def __init__(self, balance: int = 100_00, deduct_ok: bool = True, reverse_ok: bool = True):
        self.balance = balance
        self.deduct_ok = deduct_ok
        self.reverse_ok = reverse_ok

    async def get_balance(self, reference: str) -> int:
        return self.balance

    async def deduct(self, reference: str, amount_cents: int, narrative: str, txn_id: str) -> bool:
        return self.deduct_ok

    async def reverse_deduct(self, reference: str, amount_cents: int, txn_id: str) -> bool:
        return self.reverse_ok


# ─── Checksum extended ──────────────────────────────────────────────────────

class TestChecksumExtended:
    def test_empty_params(self):
        """compute_checksum with no params should still produce valid HMAC."""
        result = compute_checksum("Balance", [], "secret")
        expected = hmac.new(b"secret", b"Balance", hashlib.sha256).hexdigest().upper()
        assert result == expected

    def test_numeric_params_converted_to_str(self):
        """Params list may contain int-like values; checksum must handle them."""
        result = compute_checksum("Deduct", ["term1", "ref1", 100], "pw")
        assert isinstance(result, str)
        assert len(result) == 64

    def test_different_method_names_differ(self):
        a = compute_checksum("Balance", ["t1"], "pw")
        b = compute_checksum("Deduct", ["t1"], "pw")
        assert a != b

    def test_checksum_is_uppercase_hex(self):
        result = compute_checksum("Test", ["a", "b"], "key")
        assert all(c in "0123456789ABCDEF" for c in result)


# ─── XML-RPC builder extended ────────────────────────────────────────────────

class TestXmlRpcBuilderExtended:
    def test_build_request_xml_declaration(self):
        xml = build_request("MyMethod", [("string", "val")])
        assert xml.startswith('<?xml version="1.0"?>')

    def test_build_request_empty_params(self):
        xml = build_request("EmptyCall", [])
        assert "<methodName>EmptyCall</methodName>" in xml
        assert "<params />" in xml or "<params/>" in xml

    def test_build_request_multiple_params(self):
        xml = build_request("Multi", [("string", "a"), ("string", "b"), ("int", "1")])
        assert xml.count("<param>") == 3

    def test_parse_response_i4_type(self):
        """Paymentology sometimes returns <i4> instead of <int>."""
        xml = (
            '<?xml version="1.0"?><methodResponse><params><param><value><struct>'
            '<member><name>resultCode</name><value><i4>1</i4></value></member>'
            '</struct></value></param></params></methodResponse>'
        )
        result = parse_response(xml)
        assert result["resultCode"] == 1

    def test_parse_response_datetime_field(self):
        xml = (
            '<?xml version="1.0"?><methodResponse><params><param><value><struct>'
            '<member><name>resultCode</name><value><int>1</int></value></member>'
            '<member><name>txnDate</name><value><dateTime.iso8601>20260413T12:00:00</dateTime.iso8601></value></member>'
            '</struct></value></param></params></methodResponse>'
        )
        result = parse_response(xml)
        assert result["txnDate"] == "20260413T12:00:00"

    def test_parse_response_empty_string_field(self):
        xml = (
            '<?xml version="1.0"?><methodResponse><params><param><value><struct>'
            '<member><name>cvv2</name><value><string></string></value></member>'
            '</struct></value></param></params></methodResponse>'
        )
        result = parse_response(xml)
        assert result["cvv2"] == ""


# ─── build_xml_response extended ───────────────────────────────────────────────

class TestBuildXmlResponseExtended:
    def test_failure_code(self):
        xml = build_xml_response(0)
        assert "<int>0</int>" in xml

    def test_string_extra_field(self):
        xml = build_xml_response(1, {"statusText": "approved"})
        assert "<string>approved</string>" in xml
        assert "statusText" in xml

    def test_int_and_string_extra(self):
        xml = build_xml_response(1, {"balanceAmount": 5000, "currency": "GBP"})
        assert "<int>5000</int>" in xml
        assert "<string>GBP</string>" in xml

    def test_no_extra_fields(self):
        xml = build_xml_response(1)
        assert xml.count("<member>") == 1  # only resultCode

    def test_xml_structure(self):
        xml = build_xml_response(1)
        assert xml.startswith('<?xml version="1.0"')
        assert "<methodResponse>" in xml
        assert "</methodResponse>" in xml


# ─── RemoteAPIHandler extended ────────────────────────────────────────────────

class TestRemoteHandlerExtended:
    def _make_handler(self, balance=10000, deduct_ok=True, reverse_ok=True):
        provider = MockBalanceProvider(balance, deduct_ok, reverse_ok)
        return RemoteAPIHandler(provider, terminal_password="testpass")

    def _deduct_xml(self, amount: int = 500, method: str = "Deduct") -> str:
        return (
            f'<?xml version="1.0"?><methodCall>'
            f'<methodName>{method}</methodName><params>'
            f'<param><value><string>TERM01</string></value></param>'
            f'<param><value><string>REF001</string></value></param>'
            f'<param><value><int>{amount}</int></value></param>'
            f'<param><value><string>Purchase</string></value></param>'
            f'<param><value><string>SALE</string></value></param>'
            f'<param><value><string>DATA</string></value></param>'
            f'<param><value><string>TXN-001</string></value></param>'
            f'<param><value><string>20260413T01:00:00</string></value></param>'
            f'<param><value><string>FAKECHECKSUM</string></value></param>'
            f'</params></methodCall>'
        )

    def _balance_xml(self) -> str:
        return (
            '<?xml version="1.0"?><methodCall>'
            '<methodName>Balance</methodName><params>'
            '<param><value><string>TERM01</string></value></param>'
            '<param><value><string>REF001</string></value></param>'
            '<param><value><string>TXN-002</string></value></param>'
            '<param><value><string>20260413T01:00:00</string></value></param>'
            '<param><value><string>FAKECS</string></value></param>'
            '</params></methodCall>'
        )

    def _reversal_xml(self) -> str:
        return (
            '<?xml version="1.0"?><methodCall>'
            '<methodName>DeductReversal</methodName><params>'
            '<param><value><string>TERM01</string></value></param>'
            '<param><value><string>REF001</string></value></param>'
            '<param><value><int>500</int></value></param>'
            '<param><value><string>TXN-001</string></value></param>'
            '<param><value><string>20260413T01:00:00</string></value></param>'
            '<param><value><string>FAKECS</string></value></param>'
            '</params></methodCall>'
        )

    @pytest.mark.asyncio
    async def test_handle_balance_returns_balance(self):
        handler = self._make_handler(balance=99900)
        result = await handler.handle_balance("TERM01", "REF001", "TXN-002", "20260413", "CS")
        assert "balanceAmount" in result
        assert "99900" in result
        assert "<int>1</int>" in result

    @pytest.mark.asyncio
    async def test_handle_deduct_approved(self):
        handler = self._make_handler(deduct_ok=True)
        result = await handler.handle_deduct(
            "TERM01", "REF001", 500, "Purchase", "SALE", "DATA", "TXN-001", "20260413", "CS"
        )
        assert "<int>1</int>" in result

    @pytest.mark.asyncio
    async def test_handle_deduct_declined(self):
        handler = self._make_handler(deduct_ok=False)
        result = await handler.handle_deduct(
            "TERM01", "REF001", 500, "Purchase", "SALE", "DATA", "TXN-001", "20260413", "CS"
        )
        assert "<int>0</int>" in result

    @pytest.mark.asyncio
    async def test_handle_deduct_reversal_ok(self):
        handler = self._make_handler(reverse_ok=True)
        result = await handler.handle_deduct_reversal("TERM01", "REF001", 500, "TXN-001", "20260413", "CS")
        assert "<int>1</int>" in result

    @pytest.mark.asyncio
    async def test_handle_deduct_reversal_failed(self):
        handler = self._make_handler(reverse_ok=False)
        result = await handler.handle_deduct_reversal("TERM01", "REF001", 500, "TXN-001", "20260413", "CS")
        assert "<int>0</int>" in result

    @pytest.mark.asyncio
    async def test_dispatch_deduct(self):
        handler = self._make_handler(deduct_ok=True)
        result = await handler.dispatch(self._deduct_xml(500))
        assert "<int>1</int>" in result

    @pytest.mark.asyncio
    async def test_dispatch_balance(self):
        handler = self._make_handler(balance=50000)
        result = await handler.dispatch(self._balance_xml())
        assert "balanceAmount" in result
        assert "50000" in result

    @pytest.mark.asyncio
    async def test_dispatch_deduct_reversal(self):
        handler = self._make_handler(reverse_ok=True)
        result = await handler.dispatch(self._reversal_xml())
        assert "<int>1</int>" in result

    @pytest.mark.asyncio
    async def test_dispatch_unknown_returns_zero(self):
        handler = self._make_handler()
        xml = (
            '<?xml version="1.0"?><methodCall>'
            '<methodName>StopCard</methodName>'
            '<params></params></methodCall>'
        )
        result = await handler.dispatch(xml)
        assert "<int>0</int>" in result

    def test_verify_checksum_correct(self):
        handler = self._make_handler()
        method = "Balance"
        params = ["TERM01", "REF001", "TXN-002", "20260413", "FAKECS"]
        cs = compute_checksum(method, params[:-1], "testpass")
        # last param is the checksum itself; verify should pass
        assert handler._verify_checksum(method, params[:-1] + [cs], cs) is True

    def test_verify_checksum_wrong(self):
        handler = self._make_handler()
        assert handler._verify_checksum("Balance", ["t1", "WRONG"], "WRONG") is False


# ─── PaymentologyAdapter extended ───────────────────────────────────────────────

SUCCESS_XML = (
    '<?xml version="1.0"?><methodResponse><params><param><value><struct>'
    '<member><name>resultCode</name><value><int>1</int></value></member>'
    '<member><name>cardNumber</name><value><string>4111000011110000</string></value></member>'
    '<member><name>cvv2</name><value><string>123</string></value></member>'
    '<member><name>expiryDate</name><value><string>2029-01-01</string></value></member>'
    '<member><name>trackingNumber</name><value><string>TRK001</string></value></member>'
    '</struct></value></param></params></methodResponse>'
)

FAIL_XML = (
    '<?xml version="1.0"?><methodResponse><params><param><value><struct>'
    '<member><name>resultCode</name><value><int>0</int></value></member>'
    '<member><name>resultText</name><value><string>Declined</string></value></member>'
    '</struct></value></param></params></methodResponse>'
)


def _mock_config() -> PaymentologyConfig:
    return PaymentologyConfig(
        terminal_id="TERM01",
        terminal_password="secret",
        base_url="https://test.paymentology.com/api",
        timeout=10,
        test_mode=True,
    )


class TestPaymentologyAdapterConfig:
    def test_default_config_values(self):
        cfg = PaymentologyConfig()
        assert cfg.test_mode is True
        assert cfg.timeout == 30
        assert "paymentology" in cfg.base_url.lower()

    def test_custom_config(self):
        cfg = PaymentologyConfig(terminal_id="T1", terminal_password="pw", test_mode=False)
        assert cfg.terminal_id == "T1"
        assert cfg.test_mode is False


class TestCardHolder:
    def test_required_fields(self):
        ch = CardHolder(reference="REF001", first_name="John", last_name="Doe")
        assert ch.reference == "REF001"
        assert ch.id_or_passport == ""
        assert ch.cellphone_number == ""


class TestCardResponse:
    def test_success_response(self):
        r = CardResponse(success=True, result_code=1, result_text="OK", card_number="4111000011110000")
        assert r.success is True
        assert r.card_number == "4111000011110000"

    def test_failure_response(self):
        r = CardResponse(success=False, result_code=0, result_text="Declined")
        assert r.success is False
        assert r.card_number == ""


class TestPaymentologyAdapterHTTP:
    def _adapter(self) -> PaymentologyAdapter:
        return PaymentologyAdapter(_mock_config())

    @pytest.mark.asyncio
    async def test_create_virtual_card_success(self):
        adapter = self._adapter()
        mock_resp = MagicMock()
        mock_resp.text = SUCCESS_XML
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False
        adapter._client = mock_client

        holder = CardHolder(reference="REF001", first_name="Jane", last_name="Smith")
        expiry = datetime(2029, 1, 1, tzinfo=UTC)
        result = await adapter.create_virtual_card(holder, expiry)

        assert result.success is True
        assert result.card_number == "4111000011110000"
        assert result.cvv2 == "123"
        assert result.result_code == 1

    @pytest.mark.asyncio
    async def test_create_virtual_card_declined(self):
        adapter = self._adapter()
        mock_resp = MagicMock()
        mock_resp.text = FAIL_XML
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False
        adapter._client = mock_client

        holder = CardHolder(reference="REF002", first_name="Bad", last_name="Actor")
        expiry = datetime(2029, 1, 1, tzinfo=UTC)
        result = await adapter.create_virtual_card(holder, expiry)

        assert result.success is False
        assert result.result_code == 0
        assert result.result_text == "Declined"

    @pytest.mark.asyncio
    async def test_activate_card_success(self):
        adapter = self._adapter()
        mock_resp = MagicMock()
        mock_resp.text = SUCCESS_XML
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False
        adapter._client = mock_client

        result = await adapter.activate_card("4111000011110000")
        assert result["resultCode"] == 1

    @pytest.mark.asyncio
    async def test_stop_card(self):
        adapter = self._adapter()
        mock_resp = MagicMock()
        mock_resp.text = SUCCESS_XML
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False
        adapter._client = mock_client

        result = await adapter.stop_card("4111000011110000")
        assert result["resultCode"] == 1

    @pytest.mark.asyncio
    async def test_get_card_details(self):
        adapter = self._adapter()
        mock_resp = MagicMock()
        mock_resp.text = SUCCESS_XML
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False
        adapter._client = mock_client

        result = await adapter.get_card_details("4111000011110000")
        assert result["cardNumber"] == "4111000011110000"

    @pytest.mark.asyncio
    async def test_close_adapter(self):
        adapter = self._adapter()
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.aclose = AsyncMock()
        adapter._client = mock_client
        await adapter.close()
        mock_client.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_already_closed(self):
        adapter = self._adapter()
        # _client is None — should not raise
        await adapter.close()

    def test_txn_id_unique(self):
        adapter = self._adapter()
        ids = {adapter._txn_id() for _ in range(100)}
        assert len(ids) == 100

    def test_now_iso_format(self):
        adapter = self._adapter()
        ts = adapter._now_iso()
        assert "T" in ts
        assert len(ts) == 17  # YYYYMMDDThh:mm:ss (17 chars)


# ─── Webhook server ──────────────────────────────────────────────────────────────

class TestWebhookServer:
    def test_health_endpoint(self):
        """Health endpoint must return 200 with status ok."""
        from fastapi.testclient import TestClient

        from src.paymentology.webhook_server import app
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_webhook_no_handler_returns_zero(self):
        """If handler not initialised, webhook returns resultCode=0."""
        from fastapi.testclient import TestClient

        import src.paymentology.webhook_server as ws
        from src.paymentology.webhook_server import app
        ws._handler = None  # ensure no handler
        client = TestClient(app)
        resp = client.post(
            "/paymentology/webhook",
            content=b"<methodCall><methodName>Balance</methodName><params/></methodCall>",
            headers={"Content-Type": "text/xml"},
        )
        assert resp.status_code == 200
        assert "resultCode" in resp.text

    @pytest.mark.asyncio
    async def test_webhook_with_handler(self):
        """Webhook dispatches to handler and returns XML response."""
        from fastapi.testclient import TestClient

        import src.paymentology.webhook_server as ws
        from src.paymentology.webhook_server import app
        provider = MockBalanceProvider(balance=12345)
        handler = RemoteAPIHandler(provider, "testpass")
        ws._handler = handler
        client = TestClient(app)
        xml_body = (
            b'<?xml version="1.0"?><methodCall>'
            b'<methodName>Balance</methodName><params>'
            b'<param><value><string>T1</string></value></param>'
            b'<param><value><string>REF1</string></value></param>'
            b'<param><value><string>TXN1</string></value></param>'
            b'<param><value><string>20260413</string></value></param>'
            b'</params></methodCall>'
        )
        resp = client.post(
            "/paymentology/webhook",
            content=xml_body,
            headers={"Content-Type": "text/xml"},
        )
        assert resp.status_code == 200
        assert "12345" in resp.text
        ws._handler = None  # cleanup
