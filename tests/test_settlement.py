"""Tests for Mastercard IPM Settlement parser and reconciler."""
from src.settlement.ipm_parser import IPMParser, IPMRecord
from src.settlement.reconciler import InternalTransaction, Reconciler, ReconciliationResult


class TestIPMRecord:
    def test_pan_masked(self):
        r = IPMRecord(de2_pan="5412345678901234")
        assert r.pan_masked == "541234****1234"

    def test_pan_masked_short(self):
        r = IPMRecord(de2_pan="1234")
        assert r.pan_masked == "****"

    def test_is_presentment(self):
        assert IPMRecord(mti="1240").is_presentment is True
        assert IPMRecord(mti="1442").is_presentment is False

    def test_is_fee(self):
        assert IPMRecord(mti="1442").is_fee is True
        assert IPMRecord(mti="1240").is_fee is False

    def test_currency_alpha(self):
        assert IPMRecord(de49_txn_currency="826").currency_alpha == "GBP"
        assert IPMRecord(de49_txn_currency="840").currency_alpha == "USD"
        assert IPMRecord(de49_txn_currency="999").currency_alpha == "999"


class TestIPMParser:
    def test_parse_empty(self):
        parser = IPMParser()
        records = parser.parse_lines([])
        assert records == []

    def test_parse_header_trailer(self):
        parser = IPMParser()
        lines = ["1100HEADER_DATA", "1820TRAILER_DATA"]
        records = parser.parse_lines(lines)
        assert len(records) == 0
        assert parser.header["mti"] == "1100"
        assert parser.trailer["mti"] == "1820"

    def test_parse_presentment(self):
        line = "1240\x1c025412345678901234\x1c04000010000\x1c49826\x1c38ABC123"
        parser = IPMParser()
        records = parser.parse_lines([line])
        assert len(records) == 1
        assert records[0].mti == "1240"
        assert records[0].de2_pan == "5412345678901234"
        assert records[0].de4_txn_amount == 10000
        assert records[0].de49_txn_currency == "826"
        assert records[0].de38_approval_code == "ABC123"

    def test_parse_fee(self):
        line = "1442\x1c04000005000\x1c49826"
        parser = IPMParser()
        records = parser.parse_lines([line])
        assert len(records) == 1
        assert records[0].is_fee is True

    def test_summary(self):
        lines = [
            "1240\x1c04000010000\x1c49826",
            "1240\x1c04000020000\x1c49840",
            "1442\x1c04000005000\x1c49826",
        ]
        parser = IPMParser()
        parser.parse_lines(lines)
        s = parser.summary()
        assert s["total_records"] == 3
        assert s["presentments"] == 2
        assert s["fees"] == 1


class TestReconciler:
    def test_match_by_approval_code(self):
        ipm = IPMRecord(mti="1240", de2_pan="5412345678901234",
                        de4_txn_amount=10000, de38_approval_code="ABC123",
                        de49_txn_currency="826")
        txn = InternalTransaction(txn_id="t1", pan_last4="1234",
                                  amount_cents=10000, currency="GBP",
                                  approval_code="ABC123")
        rec = Reconciler()
        results = rec.reconcile([ipm], [txn])
        assert len(results) == 1
        assert results[0].status == "matched"

    def test_unmatched(self):
        ipm = IPMRecord(mti="1240", de2_pan="5412345678901234",
                        de4_txn_amount=10000, de38_approval_code="XYZ999",
                        de49_txn_currency="826")
        rec = Reconciler()
        results = rec.reconcile([ipm], [])
        assert results[0].status == "unmatched"

    def test_amount_discrepancy(self):
        ipm = IPMRecord(mti="1240", de2_pan="5412345678901234",
                        de4_txn_amount=10000, de38_approval_code="ABC123",
                        de49_txn_currency="826")
        txn = InternalTransaction(txn_id="t1", pan_last4="1234",
                                  amount_cents=9500, currency="GBP",
                                  approval_code="ABC123")
        rec = Reconciler()
        results = rec.reconcile([ipm], [txn])
        assert results[0].status == "discrepancy"
        assert any("amount" in d for d in results[0].discrepancies)

    def test_summary(self):
        r1 = ReconciliationResult(ipm_record=IPMRecord(), status="matched")
        r2 = ReconciliationResult(ipm_record=IPMRecord(), status="unmatched")
        r3 = ReconciliationResult(ipm_record=IPMRecord(), status="discrepancy",
                                  discrepancies=["amount mismatch"])
        s = Reconciler.summary([r1, r2, r3])
        assert s["matched"] == 1
        assert s["unmatched"] == 1
        assert s["discrepancies"] == 1
        assert s["match_rate"] == "33.3%"
