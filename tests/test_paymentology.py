"""Tests for Paymentology Companion API adapter."""
import hashlib
import hmac

import pytest

from src.paymentology.checksum import compute_checksum
from src.paymentology.remote_handler import build_xml_response
from src.paymentology.xmlrpc_builder import build_request, parse_response


class TestChecksum:
    def test_compute_checksum_format(self):
        result = compute_checksum("CreateLinkedCard", ["001", "ref1", "John"], "secret")
        assert isinstance(result, str)
        assert result == result.upper()
        assert len(result) == 64

    def test_compute_checksum_deterministic(self):
        a = compute_checksum("Deduct", ["t1", "r1", "100"], "pass")
        b = compute_checksum("Deduct", ["t1", "r1", "100"], "pass")
        assert a == b

    def test_compute_checksum_different_password(self):
        a = compute_checksum("Deduct", ["t1"], "pass1")
        b = compute_checksum("Deduct", ["t1"], "pass2")
        assert a != b

    def test_checksum_matches_manual(self):
        msg = "CreateLinkedCard" + "001" + "ref1"
        expected = hmac.new(b"secret", msg.encode(), hashlib.sha256).hexdigest().upper()
        result = compute_checksum("CreateLinkedCard", ["001", "ref1"], "secret")
        assert result == expected


class TestXmlRpcBuilder:
    def test_build_request_basic(self):
        xml = build_request("TestMethod", [("string", "val1"), ("int", "42")])
        assert "<methodName>TestMethod</methodName>" in xml
        assert "<string>val1</string>" in xml
        assert "<int>42</int>" in xml

    def test_parse_response_success(self):
        xml = ('<?xml version="1.0"?><methodResponse><params><param><value><struct>'
               '<member><name>resultCode</name><value><int>1</int></value></member>'
               '<member><name>cardNumber</name><value><string>4111222233334444</string></value></member>'
               '</struct></value></param></params></methodResponse>')
        result = parse_response(xml)
        assert result["resultCode"] == 1
        assert result["cardNumber"] == "4111222233334444"

    def test_parse_response_fault(self):
        xml = ('<methodResponse><fault><value><struct>'
               '<member><name>faultCode</name><value><int>1</int></value></member>'
               '</struct></value></fault></methodResponse>')
        result = parse_response(xml)
        assert result.get("error") is True


class TestBuildXmlResponse:
    def test_basic_response(self):
        xml = build_xml_response(1)
        assert "<int>1</int>" in xml
        assert "resultCode" in xml

    def test_response_with_extra(self):
        xml = build_xml_response(1, {"balanceAmount": 50000})
        assert "balanceAmount" in xml
        assert "50000" in xml


class TestRemoteHandler:
    @pytest.mark.asyncio
    async def test_dispatch_unknown_method(self):
        class MockProvider:
            async def get_balance(self, ref): return 0
            async def deduct(self, ref, amt, nar, tid): return False
            async def reverse_deduct(self, ref, amt, tid): return False
        from src.paymentology.remote_handler import RemoteAPIHandler
        handler = RemoteAPIHandler(MockProvider(), "testpass")
        xml = ('<?xml version="1.0"?><methodCall><methodName>UnknownMethod</methodName>'
               '<params></params></methodCall>')
        result = await handler.dispatch(xml)
        assert "<int>0</int>" in result
