"""Mastercard IPM/T112 settlement file parser.

WHY: ADR-015 — Settlement = core (незаменяемое ядро, I-12, I-20).
Mastercard settlement files are in ISO 8583 bit-mapped format (IPM).
No open-source parser exists — this is proprietary BANXE core.

I-24: All parsed records are append-only (written to ClickHouse).
I-12: This validator is the source of truth for settlement amounts.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from enum import Enum


class TransactionType(Enum):
    PURCHASE = "0200"
    REFUND = "0420"
    REVERSAL = "0400"
    FEE = "0620"


@dataclass
class IPMRecord:
    """A single parsed transaction record from a Mastercard IPM file."""

    message_type: TransactionType
    transaction_id: str  # DE 37: retrieval reference number
    pan_masked: str  # DE 2: PAN (masked, last 4 only)
    amount_minor: int  # DE 4: transaction amount in minor units (I-05: int)
    currency_code: str  # DE 49: ISO 4217 numeric → alpha
    settlement_date: date  # DE 15: settlement date
    merchant_id: str  # DE 42: card acceptor ID
    merchant_name: str  # DE 43: card acceptor name/location
    interchange_fee_minor: int  # DE 28: transaction fee in minor units
    authorisation_code: str  # DE 38: approval code
    raw_hex: str  # original record for audit (I-24)


class IPMParser:
    """Minimal Mastercard IPM settlement file parser.

    Production usage:
        with open("settlement_20260412.ipm", "rb") as f:
            for record in IPMParser().parse(f.read()):
                write_to_clickhouse(record)  # I-24: append-only

    Sprint 11 deliverable — see ADR-015 implementation plan.
    """

    # ISO 4217 numeric to alpha mapping (subset for common currencies)
    _CURRENCY_MAP: dict[str, str] = {
        "826": "GBP",
        "978": "EUR",
        "840": "USD",
        "756": "CHF",
        "208": "DKK",
        "752": "SEK",
        "578": "NOK",
        "985": "PLN",
    }

    def parse(self, raw_bytes: bytes) -> Iterator[IPMRecord]:
        """Parse a Mastercard IPM binary file.

        Yields one IPMRecord per transaction record.
        Raises ValueError on malformed records (fail-loud, I-12).

        NOTE: This is a stub. Full implementation requires Mastercard
        IFF (Interchange File Format) specification (NDA-protected).
        Sprint 11 deliverable.
        """
        if not raw_bytes:
            return  # empty file → no records

        # Stub: real implementation parses ISO 8583 bit-mapped records
        yield from ()  # make this a generator function
        raise NotImplementedError(
            "IPMParser.parse() — Sprint 11 deliverable. "
            "Requires Mastercard IFF specification access."
        )

    def _decode_currency(self, numeric_code: str) -> str:
        return self._CURRENCY_MAP.get(numeric_code, numeric_code)

    @staticmethod
    def _parse_amount(raw: bytes) -> int:
        """Convert IPM amount field (BCD) to integer minor units. Never float (I-05)."""
        return int(raw.hex())
