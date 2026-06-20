"""MIG-M2.6 — advisory SEPA outgoing rail-consumer scaffold (no live execution, consumes M2.1 engine).

characterization: SepaRailPort / DTO / state-machine shape; deterministic. contract: consumes a
PaymentEnginePort intent (account refs + amount); webhook/file idempotency (same key -> same rail
intent, no dup); projection by account_ref (NO balance fields). fence: no ledger/midaz/fee/
payment_switch/live-papaya/live-swift imports; amount_minor int (I-05), never float.
"""

from dataclasses import fields
from pathlib import Path

from src.payments.sandbox_engine import SandboxPaymentEngine
from src.payments.sepa_sandbox_rail import SandboxSepaRail
from src.ports.sepa_rail_port import (
    RAIL_TRANSITIONS,
    SepaOutgoingIntent,
    SepaRailPort,
    SepaRailState,
    SepaRailStatusRecord,
)

_BALANCE_FIELDS = {"balance", "available", "ledger_balance", "balance_minor"}


def _wired() -> tuple[SandboxPaymentEngine, SandboxSepaRail, str]:
    eng = SandboxPaymentEngine()
    intent = eng.create_intent(
        idempotency_key="pk1",
        debtor_account_ref="ACC-A",
        creditor_account_ref="ACC-B",
        amount_minor=2500,
        currency="EUR",
    )
    return eng, SandboxSepaRail(eng), intent.intent_id


def test_port_and_state_machine_shape() -> None:
    assert issubclass(SandboxSepaRail, SepaRailPort)
    assert {s.value for s in SepaRailState} == {
        "submitted",
        "pacs_generated",
        "sent_to_rail",
        "ack",
        "settled",
        "returned",
        "rejected",
    }
    assert RAIL_TRANSITIONS[SepaRailState.SETTLED] == ()
    assert RAIL_TRANSITIONS[SepaRailState.REJECTED] == ()


def test_submit_consumes_payment_intent() -> None:
    _eng, rail, pid = _wired()
    out = rail.submit_outgoing(
        file_dedup_key="f1", payment_intent_id=pid, creditor_iban="DE89370400440532013000"
    )
    assert isinstance(out, SepaOutgoingIntent)
    assert out.payment_intent_id == pid  # consumed from PaymentEnginePort
    assert out.debtor_account_ref == "ACC-A" and out.amount_minor == 2500 and out.currency == "EUR"
    assert out.state is SepaRailState.SUBMITTED
    st = rail.get_rail_status(out.rail_intent_id)
    assert isinstance(st, SepaRailStatusRecord) and st.state is SepaRailState.SUBMITTED


def test_file_idempotency_no_duplicate() -> None:
    _eng, rail, pid = _wired()
    a = rail.submit_outgoing(file_dedup_key="dup", payment_intent_id=pid, creditor_iban="DE00")
    b = rail.submit_outgoing(file_dedup_key="dup", payment_intent_id=pid, creditor_iban="DE00")
    assert a.rail_intent_id == b.rail_intent_id
    assert len(rail.list_outgoing()) == 1


def test_projection_no_balance_fields() -> None:
    for dc in (SepaOutgoingIntent, SepaRailStatusRecord):
        assert _BALANCE_FIELDS.isdisjoint({f.name for f in fields(dc)})


def test_unknown_intent_fail_closed() -> None:
    import pytest

    eng = SandboxPaymentEngine()
    rail = SandboxSepaRail(eng)
    with pytest.raises(KeyError):
        rail.submit_outgoing(
            file_dedup_key="x", payment_intent_id="PI-unknown", creditor_iban="DE00"
        )
    assert rail.get_rail_status("SEPA-OUT-unknown") is None


def test_fence_no_live_rail_or_ledger_imports() -> None:
    for mod in ("src/ports/sepa_rail_port.py", "src/payments/sepa_sandbox_rail.py"):
        import_lines = "\n".join(
            ln
            for ln in Path(mod).read_text().splitlines()
            if ln.strip().startswith(("import ", "from "))
        ).lower()
        for bad in (
            "ledger_port",
            "midaz",
            "fee",
            "payment_switch",
            "papaya",
            "swift",
            "httpx",
            "requests",
        ):
            assert bad not in import_lines, f"forbidden import token: {bad}"
