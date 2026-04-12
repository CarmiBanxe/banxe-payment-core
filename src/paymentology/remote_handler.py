"""Remote API Handler — webhook for Paymentology callbacks.

Paymentology calls YOUR server for: Deduct, Balance, DeductReversal, etc.
You must respond with XML-RPC methodResponse.
"""
import logging
from typing import Protocol
from .checksum import compute_checksum
from .xmlrpc_builder import build_request, parse_response

logger = logging.getLogger(__name__)


class BalanceProvider(Protocol):
    async def get_balance(self, reference: str) -> int:
        """Return available balance in cents."""
        ...

    async def deduct(self, reference: str, amount_cents: int, narrative: str, txn_id: str) -> bool:
        """Deduct amount from wallet. Return True if approved."""
        ...

    async def reverse_deduct(self, reference: str, amount_cents: int, txn_id: str) -> bool:
        """Reverse a previous deduction."""
        ...


def build_xml_response(result_code: int, extra: dict | None = None) -> str:
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<methodResponse><params><param><value><struct>",
        f"<member><name>resultCode</name><value><int>{result_code}</int></value></member>",
    ]
    if extra:
        for k, v in extra.items():
            if isinstance(v, int):
                parts.append(f"<member><name>{k}</name><value><int>{v}</int></value></member>")
            else:
                parts.append(f"<member><name>{k}</name><value><string>{v}</string></value></member>")
    parts.append("</struct></value></param></params></methodResponse>")
    return "".join(parts)


class RemoteAPIHandler:
    """Handles incoming XML-RPC calls from Paymentology."""

    def __init__(self, balance_provider: BalanceProvider, terminal_password: str):
        self.provider = balance_provider
        self.terminal_password = terminal_password

    def _verify_checksum(self, method: str, params: list[str], received: str) -> bool:
        expected = compute_checksum(method, params[:-1], self.terminal_password)
        return expected == received

    async def handle_deduct(self, terminal_id: str, reference: str,
                            amount: int, narrative: str, txn_type: str,
                            txn_data: str, txn_id: str, txn_date: str,
                            checksum: str) -> str:
        logger.info("Deduct ref=%s amount=%d narrative=%s", reference, amount, narrative)
        ok = await self.provider.deduct(reference, amount, narrative, txn_id)
        code = 1 if ok else 0
        return build_xml_response(code)

    async def handle_balance(self, terminal_id: str, reference: str,
                             txn_id: str, txn_date: str, checksum: str) -> str:
        balance = await self.provider.get_balance(reference)
        return build_xml_response(1, {"balanceAmount": balance, "actualBalance": balance})

    async def handle_deduct_reversal(self, terminal_id: str, reference: str,
                                     amount: int, txn_id: str, txn_date: str,
                                     checksum: str) -> str:
        ok = await self.provider.reverse_deduct(reference, amount, txn_id)
        return build_xml_response(1 if ok else 0)

    async def dispatch(self, xml_body: str) -> str:
        from xml.etree.ElementTree import fromstring
        root = fromstring(xml_body)
        method = root.find("methodName").text
        vals = []
        for param in root.findall(".//param/value"):
            child = list(param)
            if child:
                vals.append(child[0].text or "")
            else:
                vals.append(param.text or "")
        logger.info("Remote dispatch: %s with %d params", method, len(vals))
        if method == "Deduct" and len(vals) >= 9:
            return await self.handle_deduct(vals[0], vals[1], int(vals[2]),
                vals[3], vals[4], vals[5], vals[6], vals[7], vals[8])
        elif method == "Balance" and len(vals) >= 4:
            return await self.handle_balance(vals[0], vals[1], vals[2], vals[3],
                vals[4] if len(vals) > 4 else "")
        elif method == "DeductReversal" and len(vals) >= 5:
            return await self.handle_deduct_reversal(vals[0], vals[1], int(vals[2]),
                vals[3], vals[4], vals[5] if len(vals) > 5 else "")
        else:
            logger.warning("Unknown remote method: %s", method)
            return build_xml_response(0)
