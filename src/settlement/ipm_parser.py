"""Mastercard IPM (Integrated Processing Message) file parser.

IPM files contain fixed-length records for settlement/clearing.
Each record has a Message Type Indicator (MTI) and Data Elements (DE).

Key record types:
- 1240: Financial presentment (first/second)
- 1442: Fee collection
- 1740: Retrieval request
- 1644: Administrative message

Key data elements for reconciliation:
- DE2: Primary Account Number (PAN)
- DE3: Processing Code
- DE4: Transaction Amount
- DE5: Settlement Amount
- DE6: Cardholder Billing Amount
- DE12: Local Transaction Time
- DE13: Local Transaction Date
- DE23: Card Sequence Number
- DE24: Function Code
- DE25: Message Reason Code
- DE26: Merchant Category Code (MCC)
- DE31: Acquirer Reference Data
- DE38: Approval Code
- DE42: Card Acceptor ID
- DE48: Additional Data (subelements)
- DE49: Transaction Currency Code
- DE50: Settlement Currency Code
"""
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class IPMRecord:
    """Parsed IPM settlement record."""
    mti: str = ""                    # Message Type Indicator (1240, 1442, etc)
    de2_pan: str = ""                # Primary Account Number (masked)
    de3_proc_code: str = ""          # Processing Code
    de4_txn_amount: int = 0          # Transaction Amount (cents)
    de5_settlement_amount: int = 0   # Settlement Amount (cents)
    de6_billing_amount: int = 0      # Cardholder Billing Amount (cents)
    de12_time: str = ""              # Local Transaction Time (HHmmss)
    de13_date: str = ""              # Local Transaction Date (MMDD)
    de24_function_code: str = ""     # Function Code
    de25_reason_code: str = ""       # Message Reason Code
    de26_mcc: str = ""               # Merchant Category Code
    de31_acquirer_ref: str = ""      # Acquirer Reference Data
    de38_approval_code: str = ""     # Approval Code
    de42_acceptor_id: str = ""       # Card Acceptor ID
    de49_txn_currency: str = ""      # Transaction Currency (ISO numeric)
    de50_settle_currency: str = ""   # Settlement Currency (ISO numeric)
    de63_trace_id: str = ""          # Transaction lifecycle trace ID
    raw_line: str = ""               # Original raw record
    line_number: int = 0

    @property
    def pan_masked(self) -> str:
        if len(self.de2_pan) > 8:
            return self.de2_pan[:6] + "****" + self.de2_pan[-4:]
        return "****"

    @property
    def txn_date_formatted(self) -> str:
        if self.de13_date and self.de12_time:
            return f"{self.de13_date} {self.de12_time}"
        return self.de13_date or "unknown"

    @property
    def is_presentment(self) -> bool:
        return self.mti == "1240"

    @property
    def is_fee(self) -> bool:
        return self.mti == "1442"

    @property
    def currency_alpha(self) -> str:
        return CURRENCY_MAP.get(self.de49_txn_currency, self.de49_txn_currency)


CURRENCY_MAP = {
    "826": "GBP", "840": "USD", "978": "EUR",
    "756": "CHF", "392": "JPY", "036": "AUD",
    "124": "CAD", "344": "HKD", "702": "SGD",
}


class IPMParser:
    """Parser for Mastercard IPM settlement files.

    Supports both fixed-length and delimited formats.
    """

    def __init__(self):
        self.records: list[IPMRecord] = []
        self.errors: list[str] = []
        self.header: dict = {}
        self.trailer: dict = {}

    def parse_file(self, filepath: str | Path) -> list[IPMRecord]:
        filepath = Path(filepath)
        if not filepath.exists():
            raise FileNotFoundError(f"IPM file not found: {filepath}")
        logger.info("Parsing IPM file: %s", filepath)
        with open(filepath, encoding="latin-1") as f:
            lines = f.readlines()
        return self.parse_lines(lines)

    def parse_lines(self, lines: list[str]) -> list[IPMRecord]:
        self.records = []
        self.errors = []
        for i, line in enumerate(lines, 1):
            line = line.rstrip("\n\r")
            if not line or len(line) < 4:
                continue
            mti = line[:4]
            if mti in ("1100", "1800"):
                self.header = {"mti": mti, "line": i, "raw": line}
                continue
            if mti in ("1820", "9999"):
                self.trailer = {"mti": mti, "line": i, "raw": line}
                continue
            try:
                record = self._parse_record(line, i)
                if record:
                    self.records.append(record)
            except Exception as e:
                self.errors.append(f"Line {i}: {e}")
                logger.warning("IPM parse error line %d: %s", i, e)
        logger.info("Parsed %d records, %d errors from %d lines",
                     len(self.records), len(self.errors), len(lines))
        return self.records

    def _parse_record(self, line: str, line_num: int) -> IPMRecord | None:
        mti = line[:4]
        if mti not in ("1240", "1442", "1740", "1644"):
            return None
        record = IPMRecord(mti=mti, raw_line=line, line_number=line_num)
        if len(line) > 4:
            self._extract_data_elements(record, line[4:])
        return record

    def _extract_data_elements(self, record: IPMRecord, data: str):
        """Extract data elements from IPM record body.

        IPM uses a bitmap + field structure. This is a simplified
        positional parser for common fields. Real production parser
        would use full ISO 8583 bitmap decoding.
        """
        fields = data.split("\x1c") if "\x1c" in data else [data]
        for f in fields:
            if not f or len(f) < 3:
                continue
            tag = f[:2]
            value = f[2:]
            if tag == "02":
                record.de2_pan = value
            elif tag == "03":
                record.de3_proc_code = value
            elif tag == "04":
                record.de4_txn_amount = self._safe_int(value)
            elif tag == "05":
                record.de5_settlement_amount = self._safe_int(value)
            elif tag == "06":
                record.de6_billing_amount = self._safe_int(value)
            elif tag == "12":
                record.de12_time = value
            elif tag == "13":
                record.de13_date = value
            elif tag == "24":
                record.de24_function_code = value
            elif tag == "25":
                record.de25_reason_code = value
            elif tag == "26":
                record.de26_mcc = value
            elif tag == "31":
                record.de31_acquirer_ref = value
            elif tag == "38":
                record.de38_approval_code = value
            elif tag == "42":
                record.de42_acceptor_id = value
            elif tag == "49":
                record.de49_txn_currency = value
            elif tag == "50":
                record.de50_settle_currency = value
            elif tag == "63":
                record.de63_trace_id = value

    @staticmethod
    def _safe_int(s: str) -> int:
        try:
            return int(s.strip())
        except (ValueError, AttributeError):
            return 0

    def summary(self) -> dict:
        total_amount = sum(r.de4_txn_amount for r in self.records)
        settle_amount = sum(r.de5_settlement_amount for r in self.records)
        by_currency = {}
        for r in self.records:
            cur = r.currency_alpha
            by_currency.setdefault(cur, {"count": 0, "amount": 0})
            by_currency[cur]["count"] += 1
            by_currency[cur]["amount"] += r.de4_txn_amount
        return {
            "total_records": len(self.records),
            "presentments": sum(1 for r in self.records if r.is_presentment),
            "fees": sum(1 for r in self.records if r.is_fee),
            "total_txn_amount_cents": total_amount,
            "total_settle_amount_cents": settle_amount,
            "by_currency": by_currency,
            "parse_errors": len(self.errors),
        }
