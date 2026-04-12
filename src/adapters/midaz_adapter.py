"""MidazAdapter — LedgerPort implementation via Midaz API (:8095).

WHY: ADR-013, ADR-014, ADR-015.
Midaz is the PRIMARY CBS (Core Banking System). All balance operations go here.
I-05: amounts are always int (minor units) — this adapter enforces it.
I-10: If MIDAZ_BASE_URL not configured, raise NotImplementedError.
"""

from __future__ import annotations

import os

import httpx

from src.ports.ledger_port import BalanceResult, LedgerEntry, LedgerPort

_DEFAULT_MIDAZ_URL = "http://localhost:8095"


class MidazAdapter(LedgerPort):
    """Concrete LedgerPort backed by Midaz REST API.

    Config (from environment / .env):
        MIDAZ_BASE_URL    — default http://localhost:8095
        MIDAZ_API_KEY     — Midaz API key
        MIDAZ_ORG_ID      — Midaz organisation ID
        MIDAZ_LEDGER_ID   — Midaz ledger ID
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        org_id: str | None = None,
        ledger_id: str | None = None,
    ) -> None:
        self._base_url = (base_url or os.environ.get("MIDAZ_BASE_URL", _DEFAULT_MIDAZ_URL)).rstrip(
            "/"
        )
        self._api_key = api_key or os.environ.get("MIDAZ_API_KEY", "")
        self._org_id = org_id or os.environ.get("MIDAZ_ORG_ID", "")
        self._ledger_id = ledger_id or os.environ.get("MIDAZ_LEDGER_ID", "")

        if not self._org_id or not self._ledger_id:  # I-10
            raise NotImplementedError(
                "MIDAZ_ORG_ID and MIDAZ_LEDGER_ID must be set. "
                "Midaz is deployed at Sprint 8 — check :8095."
            )

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    def _accounts_url(self, account_id: str) -> str:
        return (
            f"{self._base_url}/v1/organizations/{self._org_id}"
            f"/ledgers/{self._ledger_id}/accounts/{account_id}"
        )

    def _transactions_url(self) -> str:
        return (
            f"{self._base_url}/v1/organizations/{self._org_id}"
            f"/ledgers/{self._ledger_id}/transactions"
        )

    async def get_balance(self, account_id: str, currency: str) -> BalanceResult:
        async with httpx.AsyncClient(headers=self._headers) as client:
            resp = client.get(self._accounts_url(account_id))
            resp.raise_for_status()
            data = resp.json()
        balance = data.get("balance", {})
        return BalanceResult(
            account_id=account_id,
            available_minor=int(balance.get("available", 0)),
            pending_minor=int(balance.get("onHold", 0)),
            currency=currency,
        )

    async def debit(self, entry: LedgerEntry) -> str:
        return await self._post_transaction(entry, operation="DEBIT")

    async def credit(self, entry: LedgerEntry) -> str:
        return await self._post_transaction(entry, operation="CREDIT")

    async def reserve(self, entry: LedgerEntry) -> str:
        return await self._post_transaction(entry, operation="RESERVE")

    async def release_reservation(self, reservation_id: str) -> None:
        async with httpx.AsyncClient(headers=self._headers) as client:
            resp = client.patch(
                f"{self._transactions_url()}/{reservation_id}",
                json={"status": "RELEASED"},
            )
            resp.raise_for_status()

    async def settle_reservation(
        self, reservation_id: str, actual_amount_minor: int | None = None
    ) -> str:
        payload: dict = {"status": "SETTLED"}
        if actual_amount_minor is not None:
            payload["amount"] = actual_amount_minor
        async with httpx.AsyncClient(headers=self._headers) as client:
            resp = client.patch(f"{self._transactions_url()}/{reservation_id}", json=payload)
            resp.raise_for_status()
            data = resp.json()
        return data.get("id", reservation_id)

    async def _post_transaction(self, entry: LedgerEntry, operation: str) -> str:
        payload = {
            "description": entry.reference,
            "metadata": entry.metadata or {},
            "idempotencyKey": entry.idempotency_key,
            "send": {
                "accountId": entry.account_id,
                "amount": {"asset": entry.currency, "value": abs(entry.amount_minor)},
            },
            "operations": [
                {
                    "accountId": entry.account_id,
                    "amount": {"asset": entry.currency, "value": abs(entry.amount_minor)},
                    "type": operation,
                }
            ],
        }
        async with httpx.AsyncClient(headers=self._headers) as client:
            resp = client.post(self._transactions_url(), json=payload)
            resp.raise_for_status()
            data = resp.json()
        return data["id"]
