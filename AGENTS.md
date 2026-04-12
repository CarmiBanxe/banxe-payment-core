# AGENTS — banxe-payment-core

## Purpose
Payment orchestration core for TOMPAY UK EMI (Principal Member Mastercard).
Implements ADR-015: Hyperswitch switch, Paymentology issuer, Midaz ledger, custom Mastercard IPM settlement.

## Architecture (Ports & Adapters)

```
AuthEngine → ComplianceScreening → :8093 compliance API
           → IssuerPort (PaymentologyAdapter) → Paymentology Companion API
           → LedgerPort (MidazAdapter) → :8095 Midaz
PaymentSwitchPort (HyperswitchAdapter) → :8096 Hyperswitch
SettlementReconciler ← IPMParser (ISO 8583 / Sprint 11)
```

## Invariants (NEVER violate)

| Code | Rule |
|------|------|
| I-01 | Sanctions screening FIRST — before any authorisation |
| I-02 | Category A (RU/BY/IR/KP/CU/MM/AF) → immediate BLOCK |
| I-04 | ≥£10,000 → REFER (HITL) |
| I-05 | Monetary amounts = `int` minor units (pence), never `float` |
| I-10 | No fake integrations — raise `NotImplementedError` if env not configured |
| I-15 | No AGPLv3 dependencies |
| I-20 | Each component independently replaceable via port |
| I-24 | Decision log = append-only (ClickHouse) |

## Agents in this repo

### AuthEngine (`src/authorization/auth_engine.py`)
Orchestrates the full authorisation flow: compliance → HITL check → balance → approve.

### ComplianceScreening (`src/compliance_bridge/screening.py`)
Pre-auth sanctions and jurisdiction check. Calls compliance API at :8093.

### HyperswitchAdapter (`src/adapters/hyperswitch_adapter.py`)
Payment switch via Hyperswitch REST API at :8096 (Sprint 9).

### PaymentologyAdapter (`src/adapters/paymentology_adapter.py`)
Card issuing via Paymentology. `authorise_transaction` → auth_engine decides (Sprint 10).

### MidazAdapter (`src/adapters/midaz_adapter.py`)
Double-entry ledger via Midaz at :8095 (deployed Sprint 8).

### IPMParser (`src/settlement/mastercard_ipm_parser.py`)
Mastercard IPM/T112 (ISO 8583) settlement file parser (Sprint 11 stub).

### SettlementReconciler (`src/settlement/reconciliation.py`)
Reconciles IPM records against Midaz ledger balances.

## Sprint Roadmap

| Sprint | Deliverable |
|--------|-------------|
| 9 | Hyperswitch deployed, HyperswitchAdapter wired, payment flow E2E |
| 10 | Paymentology Companion API, AuthEngine live, card authorisations |
| 11 | IPMParser (ISO 8583), SettlementReconciler, daily settlement run |
| 12 | ClickHouse decision log (I-24), monitoring, production hardening |

## Running tests
```bash
python -m pytest tests/ -q          # all tests
make quality-gate                   # lint + test + coverage ≥80%
```

## Related ADRs
- ADR-015: Payment Processing Stack (Hyperswitch + Paymentology + Midaz)
- ADR-013: Midaz as Ledger
- ADR-011: Reference vs Operational Dependency classification
