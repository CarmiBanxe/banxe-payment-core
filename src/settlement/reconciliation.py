"""Settlement reconciliation — IPM records vs Midaz ledger (ADR-015, I-12).

WHY: Every settled transaction must match a ledger entry in Midaz.
Discrepancies trigger an alert and HITL review (I-04).
I-24: Reconciliation results are append-only in ClickHouse.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.ports.ledger_port import LedgerPort
from src.settlement.mastercard_ipm_parser import IPMRecord


class ReconciliationStatus(Enum):
    MATCHED = "MATCHED"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    MISSING_IN_LEDGER = "MISSING_IN_LEDGER"
    MISSING_IN_IPM = "MISSING_IN_IPM"


@dataclass
class ReconciliationResult:
    transaction_id: str
    status: ReconciliationStatus
    ipm_amount_minor: int | None
    ledger_amount_minor: int | None
    currency: str
    discrepancy_minor: int = 0


class SettlementReconciler:
    """Reconcile Mastercard IPM settlement records against Midaz ledger.

    Sprint 11 deliverable — called nightly after IPM file receipt.
    """

    def __init__(self, ledger: LedgerPort) -> None:
        self._ledger = ledger

    async def reconcile(self, ipm_records: list[IPMRecord]) -> list[ReconciliationResult]:
        """Compare IPM records to ledger entries. Returns all results.

        Raises: Nothing — fail-soft, log discrepancies for HITL review.
        All results are written to ClickHouse (I-24) by the caller.
        """
        results: list[ReconciliationResult] = []

        for record in ipm_records:
            result = await self._reconcile_one(record)
            results.append(result)

        return results

    async def _reconcile_one(self, record: IPMRecord) -> ReconciliationResult:
        """Reconcile a single IPM record vs ledger."""
        try:
            balance = await self._ledger.get_balance(
                account_id=record.transaction_id,
                currency=record.currency_code,
            )
            # Simplified: compare transaction amounts
            # Production: query ledger by transaction_id reference
            if balance.available_minor == record.amount_minor:
                return ReconciliationResult(
                    transaction_id=record.transaction_id,
                    status=ReconciliationStatus.MATCHED,
                    ipm_amount_minor=record.amount_minor,
                    ledger_amount_minor=balance.available_minor,
                    currency=record.currency_code,
                )
            return ReconciliationResult(
                transaction_id=record.transaction_id,
                status=ReconciliationStatus.AMOUNT_MISMATCH,
                ipm_amount_minor=record.amount_minor,
                ledger_amount_minor=balance.available_minor,
                currency=record.currency_code,
                discrepancy_minor=abs(record.amount_minor - balance.available_minor),
            )
        except Exception:
            return ReconciliationResult(
                transaction_id=record.transaction_id,
                status=ReconciliationStatus.MISSING_IN_LEDGER,
                ipm_amount_minor=record.amount_minor,
                ledger_amount_minor=None,
                currency=record.currency_code,
            )
