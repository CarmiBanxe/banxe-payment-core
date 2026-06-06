"""Reconciler — matches IPM settlement records against Paymentology transactions.

Compares Mastercard IPM file records with internal transaction records
to identify: matched, unmatched, and discrepancies.
"""
import logging
from dataclasses import dataclass, field

from .ipm_parser import IPMRecord

logger = logging.getLogger(__name__)


@dataclass
class InternalTransaction:
    """Internal transaction record from Hyperswitch/Paymentology."""
    txn_id: str
    pan_last4: str
    amount_cents: int
    currency: str
    approval_code: str = ""
    merchant_id: str = ""
    txn_date: str = ""
    status: str = "settled"


@dataclass
class ReconciliationResult:
    """Result of matching one IPM record."""
    ipm_record: IPMRecord
    internal_txn: InternalTransaction | None = None
    status: str = "unmatched"  # matched, unmatched, discrepancy
    discrepancies: list[str] = field(default_factory=list)


class Reconciler:
    """Matches IPM settlement records against internal transactions."""

    def __init__(self, tolerance_cents: int = 0):
        self.tolerance = tolerance_cents

    def reconcile(
        self,
        ipm_records: list[IPMRecord],
        internal_txns: list[InternalTransaction],
    ) -> list[ReconciliationResult]:
        results = []
        txn_index = self._build_index(internal_txns)

        for ipm in ipm_records:
            if not ipm.is_presentment:
                continue
            result = self._match(ipm, txn_index)
            results.append(result)

        matched = sum(1 for r in results if r.status == "matched")
        discrepancy = sum(1 for r in results if r.status == "discrepancy")
        unmatched = sum(1 for r in results if r.status == "unmatched")
        logger.info("Reconciliation: %d matched, %d discrepancy, %d unmatched",
                     matched, discrepancy, unmatched)
        return results

    def _build_index(self, txns: list[InternalTransaction]) -> dict:
        index = {}
        for txn in txns:
            key = f"{txn.approval_code}:{txn.pan_last4}"
            index.setdefault(key, []).append(txn)
            key2 = f"{txn.amount_cents}:{txn.pan_last4}"
            index.setdefault(key2, []).append(txn)
        return index

    def _match(self, ipm: IPMRecord, index: dict) -> ReconciliationResult:
        pan_last4 = ipm.de2_pan[-4:] if len(ipm.de2_pan) >= 4 else ""

        # Match by approval code + PAN last 4
        key1 = f"{ipm.de38_approval_code}:{pan_last4}"
        candidates = index.get(key1, [])

        if not candidates:
            key2 = f"{ipm.de4_txn_amount}:{pan_last4}"
            candidates = index.get(key2, [])

        if not candidates:
            return ReconciliationResult(ipm_record=ipm, status="unmatched")

        txn = candidates[0]
        discrepancies = []

        if abs(ipm.de4_txn_amount - txn.amount_cents) > self.tolerance:
            discrepancies.append(
                f"amount: IPM={ipm.de4_txn_amount} vs internal={txn.amount_cents}"
            )

        ipm_currency = ipm.currency_alpha
        if ipm_currency and ipm_currency != txn.currency:
            discrepancies.append(
                f"currency: IPM={ipm_currency} vs internal={txn.currency}"
            )

        status = "discrepancy" if discrepancies else "matched"
        return ReconciliationResult(
            ipm_record=ipm,
            internal_txn=txn,
            status=status,
            discrepancies=discrepancies,
        )

    @staticmethod
    def summary(results: list[ReconciliationResult]) -> dict:
        matched = [r for r in results if r.status == "matched"]
        unmatched = [r for r in results if r.status == "unmatched"]
        discrepancy = [r for r in results if r.status == "discrepancy"]
        return {
            "total": len(results),
            "matched": len(matched),
            "unmatched": len(unmatched),
            "discrepancies": len(discrepancy),
            "match_rate": f"{len(matched)/max(len(results),1)*100:.1f}%",
            "discrepancy_details": [
                {"line": r.ipm_record.line_number, "issues": r.discrepancies}
                for r in discrepancy
            ],
        }
