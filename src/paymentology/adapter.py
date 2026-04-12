"""PaymentologyAdapter — Local API client for Companion API."""
import uuid
import logging
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional
import httpx
from .checksum import compute_checksum
from .xmlrpc_builder import build_request, parse_response

logger = logging.getLogger(__name__)


@dataclass
class PaymentologyConfig:
    terminal_id: str = ""
    terminal_password: str = ""
    base_url: str = "https://companion-api.sprint.paymentology.com/api"
    timeout: int = 30
    test_mode: bool = True


@dataclass
class CardHolder:
    reference: str
    first_name: str
    last_name: str
    id_or_passport: str = ""
    cellphone_number: str = ""


@dataclass
class CardResponse:
    success: bool
    result_code: int
    result_text: str
    card_number: str = ""
    cvv2: str = ""
    valid_date: str = ""
    expiry_date: str = ""
    tracking_number: str = ""
    iv: str = ""


class PaymentologyAdapter:
    def __init__(self, config: PaymentologyConfig):
        self.config = config
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.config.timeout,
                headers={"Content-Type": "text/xml"},
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def _txn_id(self) -> str:
        return str(uuid.uuid4())

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H:%M:%S")

    async def _call(self, method_name: str, params: list[tuple[str, str]]) -> dict:
        xml_body = build_request(method_name, params)
        client = await self._get_client()
        logger.info("Paymentology %s -> %s", method_name, self.config.base_url)
        resp = await client.post(self.config.base_url, content=xml_body)
        resp.raise_for_status()
        result = parse_response(resp.text)
        logger.info("Paymentology %s <- resultCode=%s", method_name, result.get("resultCode"))
        return result

    async def create_virtual_card(self, holder: CardHolder, expiry_date: datetime) -> CardResponse:
        tid = self._txn_id()
        tdate = self._now_iso()
        exp_str = expiry_date.strftime("%Y-%m-%dT00:00:00")
        cs_params = [self.config.terminal_id, holder.reference, holder.first_name,
                     holder.last_name, holder.id_or_passport, holder.cellphone_number,
                     exp_str, tid, tdate]
        cs = compute_checksum("CreateLinkedCard", cs_params, self.config.terminal_password)
        params = [("string", self.config.terminal_id), ("string", holder.reference),
                  ("string", holder.first_name), ("string", holder.last_name),
                  ("string", holder.id_or_passport), ("string", holder.cellphone_number),
                  ("dateTime.iso8601", exp_str), ("string", tid),
                  ("dateTime.iso8601", tdate), ("string", cs)]
        result = await self._call("CreateLinkedCard", params)
        ok = result.get("resultCode") == 1
        return CardResponse(success=ok, result_code=result.get("resultCode", -1),
            result_text=result.get("resultText", ""), card_number=result.get("cardNumber", ""),
            cvv2=result.get("cvv2", ""), valid_date=result.get("validDate", ""),
            expiry_date=result.get("expiryDate", ""), tracking_number=result.get("trackingNumber", ""),
            iv=result.get("IV", ""))

    async def activate_card(self, card_number: str) -> dict:
        tid, tdate = self._txn_id(), self._now_iso()
        cs = compute_checksum("ActivateCard", [self.config.terminal_id, card_number, tid, tdate],
                              self.config.terminal_password)
        params = [("string", self.config.terminal_id), ("string", card_number),
                  ("string", tid), ("dateTime.iso8601", tdate), ("string", cs)]
        return await self._call("ActivateCard", params)

    async def stop_card(self, card_number: str) -> dict:
        tid, tdate = self._txn_id(), self._now_iso()
        cs = compute_checksum("StopCard", [self.config.terminal_id, card_number, tid, tdate],
                              self.config.terminal_password)
        params = [("string", self.config.terminal_id), ("string", card_number),
                  ("string", tid), ("dateTime.iso8601", tdate), ("string", cs)]
        return await self._call("StopCard", params)

    async def get_card_details(self, card_number: str) -> dict:
        tid, tdate = self._txn_id(), self._now_iso()
        cs = compute_checksum("GetCardDetails", [self.config.terminal_id, card_number, tid, tdate],
                              self.config.terminal_password)
        params = [("string", self.config.terminal_id), ("string", card_number),
                  ("string", tid), ("dateTime.iso8601", tdate), ("string", cs)]
        return await self._call("GetCardDetails", params)
