"""Tests for mastercard_ipm_parser and reconciliation modules."""
from __future__ import annotations

import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock

from src.settlement.mastercard_ipm_parser import (
    IPMParser,
    IPMRecord,
    TransactionType,
)
from src.settlement.reconciliation import (
    ReconciliationStatus,
    ReconciliationResult,
    SettlementReconciler,
)


# ---------------------------------------------------------------------------
# TransactionType enum
# ---------------------------------------------------------------------------
class TestTransactionType:
    def test_purchase_value(self):
        assert TransactionType.PURCHASE.value == "0200"

    def test_refund_value(self):
        assert TransactionType.REFUND.value == "0420"

    def test_reversal_value(self):
        assert TransactionType.REVERSAL.value == "0400"

    def test_fee_value(self):
        assert TransactionType.FEE.value == "0620"


# ---------------------------------------------------------------------------
# IPMRecord dataclass
# ---------------------------------------------------------------------------
class TestIPMRecord:
    def _make_record(self, **kwargs) -> IPMRecord:
        defaults = dict(
            message_type=TransactionType.PURCHASE,
            transaction_id="TXN123456789012",
            pan_masked="****1234",
            amount_minor=10000,
            currency_code="GBP",
            settlement_date=date(2026, 4, 12),
            merchant_id="MERCH001",
            merchant_name="ACME Store London",
            interchange_fee_minor=25,
            authorisation_code="AUTH01",
            raw_hex="deadbeef",
        )
        defaults.update(kwargs)
        return IPMRecord(**defaults)

    def test_create_record(self):
        rec = self._make_record()
        assert rec.transaction_id == "TXN123456789012"
        assert rec.amount_minor == 10000
        assert rec.currency_code == "GBP"

    def test_message_type_is_enum(self):
        rec = self._make_record()
        assert rec.message_type == TransactionType.PURCHASE

    def test_settlement_date(self):
        rec = self._make_record()
        assert rec.settlement_date == date(2026, 4, 12)

    def test_interchange_fee(self):
        rec = self._make_record(interchange_fee_minor=50)
        assert rec.interchange_fee_minor == 50

    def test_raw_hex_stored(self):
        rec = self._make_record(raw_hex="cafebabe")
        assert rec.raw_hex == "cafebabe"


# ---------------------------------------------------------------------------
# IPMParser
# ---------------------------------------------------------------------------
class TestIPMParser:
    def test_parse_empty_bytes_yields_nothing(self):
        parser = IPMParser()
        records = list(parser.parse(b""))
        assert records == []

    def test_parse_nonempty_raises_not_implemented(self):
        parser = IPMParser()
        with pytest.raises(NotImplementedError):
            list(parser.parse(b"\x00" * 128))

    def test_decode_currency_gbp(self):
        parser = IPMParser()
        assert parser._decode_currency("826") == "GBP"

    def test_decode_currency_eur(self):
        parser = IPMParser()
        assert parser._decode_currency("978") == "EUR"

    def test_decode_currency_usd(self):
        parser = IPMParser()
        assert parser._decode_currency("840") == "USD"

    def test_decode_currency_unknown_passthrough(self):
        parser = IPMParser()
        assert parser._decode_currency("999") == "999"

    def test_parse_amount_bcd(self):
        raw = bytes([0x00, 0x00, 0x01, 0x00])  # hex = "00000100" = 100
        assert IPMParser._parse_amount(raw) == 256

    def test_parse_amount_zero(self):
        raw = bytes([0x00, 0x00, 0x00, 0x00])
        assert IPMParser._parse_amount(raw) == 0

    def test_parse_amount_large(self):
        raw = bytes([0x00, 0x01, 0x86, 0xa0])  # 0x000186a0 = 100000
        assert IPMParser._parse_amount(raw) == 100000

    def test_currency_map_has_pln(self):
        parser = IPMParser()
        assert parser._decode_currency("985") == "PLN"

    def test_currency_map_has_chf(self):
        parser = IPMParser()
        assert parser._decode_currency("756") == "CHF"


# ---------------------------------------------------------------------------
# ReconciliationStatus enum
# ---------------------------------------------------------------------------
class TestReconciliationStatus:
    def test_matched(self):
        assert ReconciliationStatus.MATCHED.value == "MATCHED"

    def test_amount_mismatch(self):
        assert ReconciliationStatus.AMOUNT_MISMATCH.value == "AMOUNT_MISMATCH"

    def test_missing_in_ledger(self):
        assert ReconciliationStatus.MISSING_IN_LEDGER.value == "MISSING_IN_LEDGER"

    def test_missing_in_ipm(self):
        assert ReconciliationStatus.MISSING_IN_IPM.value == "MISSING_IN_IPM"


# ---------------------------------------------------------------------------
# ReconciliationResult dataclass
# ---------------------------------------------------------------------------
class TestReconciliationResult:
    def test_create_matched_result(self):
        r = ReconciliationResult(
            transaction_id="TX001",
            status=ReconciliationStatus.MATCHED,
            ipm_amount_minor=5000,
            ledger_amount_minor=5000,
            currency="GBP",
        )
        assert r.discrepancy_minor == 0
        assert r.status == ReconciliationStatus.MATCHED

    def test_create_mismatch_result(self):
        r = ReconciliationResult(
            transaction_id="TX002",
            status=ReconciliationStatus.AMOUNT_MISMATCH,
            ipm_amount_minor=5000,
            ledger_amount_minor=4900,
            currency="EUR",
            discrepancy_minor=100,
        )
        assert r.discrepancy_minor == 100

    def test_none_ledger_amount(self):
        r = ReconciliationResult(
            transaction_id="TX003",
            status=ReconciliationStatus.MISSING_IN_LEDGER,
            ipm_amount_minor=1000,
            ledger_amount_minor=None,
            currency="USD",
        )
        assert r.ledger_amount_minor is None


# ---------------------------------------------------------------------------
# SettlementReconciler
# ---------------------------------------------------------------------------
def _make_ipm_record(txn_id="TX001", amount=5000, currency="GBP") -> IPMRecord:
    return IPMRecord(
        message_type=TransactionType.PURCHASE,
        transaction_id=txn_id,
        pan_masked="****1234",
        amount_minor=amount,
        currency_code=currency,
        settlement_date=date(2026, 4, 12),
        merchant_id="MERCH001",
        merchant_name="Test Store",
        interchange_fee_minor=10,
        authorisation_code="A00001",
        raw_hex="aabbcc",
    )


class TestSettlementReconciler:
    def _make_ledger(self, available_minor: int):
        balance = MagicMock()
        balance.available_minor = available_minor
        ledger = MagicMock()
        ledger.get_balance = AsyncMock(return_value=balance)
        return ledger

    @pytest.mark.asyncio
    async def test_reconcile_empty_list(self):
        ledger = self._make_ledger(0)
        rec = SettlementReconciler(ledger)
        results = await rec.reconcile([])
        assert results == []

    @pytest.mark.asyncio
    async def test_reconcile_matched(self):
        ledger = self._make_ledger(5000)
        rec = SettlementReconciler(ledger)
        record = _make_ipm_record(amount=5000)
        results = await rec.reconcile([record])
        assert len(results) == 1
        assert results[0].status == ReconciliationStatus.MATCHED
        assert results[0].discrepancy_minor == 0

    @pytest.mark.asyncio
    async def test_reconcile_amount_mismatch(self):
        ledger = self._make_ledger(4900)
        rec = SettlementReconciler(ledger)
        record = _make_ipm_record(amount=5000)
        results = await rec.reconcile([record])
        assert results[0].status == ReconciliationStatus.AMOUNT_MISMATCH
        assert results[0].discrepancy_minor == 100

    @pytest.mark.asyncio
    async def test_reconcile_missing_in_ledger(self):
        ledger = MagicMock()
        ledger.get_balance = AsyncMock(side_effect=Exception("not found"))
        rec = SettlementReconciler(ledger)
        record = _make_ipm_record()
        results = await rec.reconcile([record])
        assert results[0].status == ReconciliationStatus.MISSING_IN_LEDGER
        assert results[0].ledger_amount_minor is None

    @pytest.mark.asyncio
    async def test_reconcile_multiple_records(self):
        ledger = self._make_ledger(1000)
        rec = SettlementReconciler(ledger)
        records = [
            _make_ipm_record("TX001", 1000),
            _make_ipm_record("TX002", 2000),
        ]
        results = await rec.reconcile(records)
        assert len(results) == 2
        assert results[0].status == ReconciliationStatus.MATCHED
        assert results[1].status == ReconciliationStatus.AMOUNT_MISMATCH

    @pytest.mark.asyncio
    async def test_reconcile_currency_preserved(self):
        ledger = self._make_ledger(500)
        rec = SettlementReconciler(ledger)
        record = _make_ipm_record(amount=500, currency="EUR")
        results = await rec.reconcile([record])
        assert results[0].currency == "EUR"

    @pytest.mark.asyncio
    async def test_reconcile_transaction_id_preserved(self):
        ledger = self._make_ledger(100)
        rec = SettlementReconciler(ledger)
        record = _make_ipm_record(txn_id="UNIQUE_TX_99", amount=100)
        results = await rec.reconcile([record])
        assert results[0].transaction_id == "UNIQUE_TX_99"
