"""MIG-M2.1 — advisory payments-engine scaffold (no live execution, projection over accounts SoT).

characterization: PaymentEnginePort / DTO / state-machine shape; deterministic. contract: idempotency
(same key -> same intent, no duplicate); projection by account_ref (NO balance field on DTOs). fence:
engine does NOT import LedgerPort/Midaz/FeeEngine/PaymentSwitch; amount_minor is int (I-05), never float.
"""

from dataclasses import fields
from pathlib import Path

from src.payments.sandbox_engine import SandboxPaymentEngine
from src.ports.payment_engine_port import (
    INTENT_TRANSITIONS,
    PaymentEnginePort,
    PaymentIntent,
    PaymentIntentState,
    PaymentStatusRecord,
)

_BALANCE_FIELDS = {"balance", "available", "ledger_balance", "balance_minor"}


def _engine() -> SandboxPaymentEngine:
    return SandboxPaymentEngine()


def test_port_and_state_machine_shape() -> None:
    assert issubclass(SandboxPaymentEngine, PaymentEnginePort)
    assert {s.value for s in PaymentIntentState} == {
        "created",
        "validated",
        "authorized",
        "settled",
        "failed",
        "cancelled",
    }
    # state-machine: terminal states have no outgoing transitions
    assert INTENT_TRANSITIONS[PaymentIntentState.SETTLED] == ()
    assert INTENT_TRANSITIONS[PaymentIntentState.CANCELLED] == ()


def test_create_intent_and_status() -> None:
    eng = _engine()
    pi = eng.create_intent(
        idempotency_key="k1",
        debtor_account_ref="ACC-A",
        creditor_account_ref="ACC-B",
        amount_minor=1500,
        currency="EUR",
    )
    assert isinstance(pi, PaymentIntent)
    assert pi.state is PaymentIntentState.CREATED and pi.amount_minor == 1500
    st = eng.get_status(pi.intent_id)
    assert isinstance(st, PaymentStatusRecord) and st.state is PaymentIntentState.CREATED


def test_idempotency_same_key_same_intent() -> None:
    eng = _engine()
    a = eng.create_intent(
        idempotency_key="dup",
        debtor_account_ref="A",
        creditor_account_ref="B",
        amount_minor=100,
        currency="GBP",
    )
    b = eng.create_intent(
        idempotency_key="dup",
        debtor_account_ref="A",
        creditor_account_ref="B",
        amount_minor=100,
        currency="GBP",
    )
    assert a.intent_id == b.intent_id  # no duplicate
    assert len(eng.list_intents()) == 1


def test_projection_no_balance_fields() -> None:
    # DTOs reference accounts by ref only — NO balance fields (projection over accounts SoT, not a store)
    for dc in (PaymentIntent, PaymentStatusRecord):
        names = {f.name for f in fields(dc)}
        assert _BALANCE_FIELDS.isdisjoint(names)
    pi = _engine().create_intent(
        idempotency_key="k",
        debtor_account_ref="A",
        creditor_account_ref="B",
        amount_minor=1,
        currency="USD",
    )
    assert pi.debtor_account_ref == "A" and not hasattr(pi, "balance")


def test_amount_minor_int_only_i05() -> None:
    import pytest

    with pytest.raises(TypeError):
        _engine().create_intent(
            idempotency_key="f",
            debtor_account_ref="A",
            creditor_account_ref="B",
            amount_minor=1.5,
            currency="EUR",
        )  # float rejected (I-05)


def test_get_status_fail_closed() -> None:
    assert _engine().get_status("PI-unknown") is None


def test_fence_no_ledger_fee_switch_imports() -> None:
    for mod in ("src/ports/payment_engine_port.py", "src/payments/sandbox_engine.py"):
        text = Path(mod).read_text()
        import_lines = "\n".join(
            ln for ln in text.splitlines() if ln.strip().startswith(("import ", "from "))
        ).lower()
        assert "ledger_port" not in import_lines and "midaz" not in import_lines
        assert "fee" not in import_lines and "payment_switch" not in import_lines
