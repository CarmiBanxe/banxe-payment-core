"""Tests for settlement — IPMParser and SettlementReconciler."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.ports.ledger_port import BalanceResult, LedgerPort
from src.settlement.mastercard_ipm_parser import IPMParser, IPMRecord, TransactionType
from src.settlement.reconciliation import ReconciliationStatus, SettlementReconciler


class TestIPMParser:
    def test_parse_raises_not_implemented_for_stub(self):
        """Parser is Sprint 11 deliverable — stub raises NotImplementedError."""
        parser = IPMParser()
        with pytest.raises(NotImplementedError, match="Sprint 11"):
            list(parser.parse(b"\x00\x01\x02\x03"))

    def test_parse_empty_bytes_returns_empty(self):
        """Empty input → no records (no error)."""
        parser = IPMParser()
        result = list(parser.parse(b""))
        assert result == []

    def test_decode_currency_gbp(self):
        parser = IPMParser()
        assert parser._decode_currency("826") == "GBP"

    def test_decode_currency_eur(self):
        parser = IPMParser()
        assert parser._decode_currency("978") == "EUR"

    def test_decode_currency_unknown_passthrough(self):
        parser = IPMParser()
        assert parser._decode_currency("999") == "999"

    def test_parse_amount_minor_is_int(self):
        """I-05: parsed amounts must be int, never float."""
        result = IPMParser._parse_amount(b"\x01\x00")
        assert isinstance(result, int)

    def test_ipm_record_dataclass(self):
        from datetime import date

        record = IPMRecord(
            message_type=TransactionType.PURCHASE,
            transaction_id="rrn_001",
            pan_masked="****1234",
            amount_minor=5000,
            currency_code="GBP",
            settlement_date=date(2026, 4, 12),
            merchant_id="merch_001",
            merchant_name="TESCO STORES",
            interchange_fee_minor=10,
            authorisation_code="AUTH01",
            raw_hex="deadbeef",
        )
        assert record.amount_minor == 5000
        assert isinstance(record.amount_minor, int)  # I-05


class TestSettlementReconciler:
    def _make_record(self, txn_id: str = "txn_001", amount: int = 5000):
        from datetime import date

        return IPMRecord(
            message_type=TransactionType.PURCHASE,
            transaction_id=txn_id,
            pan_masked="****1234",
            amount_minor=amount,
            currency_code="GBP",
            settlement_date=date(2026, 4, 12),
            merchant_id="merch_001",
            merchant_name="TESCO",
            interchange_fee_minor=10,
            authorisation_code="AUTH01",
            raw_hex="aabb",
        )

    @pytest.mark.asyncio
    async def test_matched_record(self):
        ledger = MagicMock(spec=LedgerPort)
        ledger.get_balance = AsyncMock(return_value=BalanceResult("txn_001", 5000, 0, "GBP"))
        reconciler = SettlementReconciler(ledger=ledger)
        results = await reconciler.reconcile([self._make_record("txn_001", 5000)])
        assert len(results) == 1
        assert results[0].status == ReconciliationStatus.MATCHED

    @pytest.mark.asyncio
    async def test_amount_mismatch(self):
        ledger = MagicMock(spec=LedgerPort)
        ledger.get_balance = AsyncMock(return_value=BalanceResult("txn_001", 4999, 0, "GBP"))
        reconciler = SettlementReconciler(ledger=ledger)
        results = await reconciler.reconcile([self._make_record("txn_001", 5000)])
        assert results[0].status == ReconciliationStatus.AMOUNT_MISMATCH
        assert results[0].discrepancy_minor == 1

    @pytest.mark.asyncio
    async def test_missing_in_ledger(self):
        ledger = MagicMock(spec=LedgerPort)
        ledger.get_balance = AsyncMock(side_effect=Exception("not found"))
        reconciler = SettlementReconciler(ledger=ledger)
        results = await reconciler.reconcile([self._make_record()])
        assert results[0].status == ReconciliationStatus.MISSING_IN_LEDGER

    @pytest.mark.asyncio
    async def test_empty_records(self):
        ledger = MagicMock(spec=LedgerPort)
        reconciler = SettlementReconciler(ledger=ledger)
        results = await reconciler.reconcile([])
        assert results == []
