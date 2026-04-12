# banxe-payment-core

**BANXE AI Bank / TOMPAY UK EMI — Payment Processing Stack**

ADR-015: Hyperswitch (payment switch) + Paymentology (issuer) + Midaz (ledger).
Principal Member Mastercard processing for TOMPAY UK EMI.

## Architecture

```
PaymentSwitchPort → HyperswitchAdapter  (:8096)
IssuerPort        → PaymentologyAdapter (Companion API)
LedgerPort        → MidazAdapter        (:8095)
```

Compliance pre-screening via `banxe-emi-stack` `:8093` (I-01 — always first).

## Quick Start

```bash
# Install dependencies
pip install -e ".[dev]"

# Run tests
make test

# Full quality gate
make quality-gate

# Start Hyperswitch stack (Sprint 9)
docker compose -f docker/docker-compose.yml up -d
```

## Key Invariants

| ID | Rule |
|----|------|
| I-01 | Sanctions screening before any payment |
| I-02 | Category A jurisdictions → always REJECT |
| I-04 | ≥£10,000 → HITL review |
| I-05 | Amounts = int (minor units), never float |
| I-15 | No AGPLv3 (Hyperswitch = Apache 2.0 ✅) |
| I-20 | Each component is independently replaceable |
| I-24 | Decision log = append-only (ClickHouse) |

## Ports

| Service | Port | Status |
|---------|------|--------|
| Hyperswitch App | 8096 | Sprint 9 |
| Hyperswitch Control Center | 8097 | Sprint 9 |
| Hyperswitch Card Vault | 8098 | Sprint 9 |
| Midaz Ledger | 8095 | ✅ Sprint 8 |
| Compliance API | 8093 | ✅ |

## Docs

- [ONBOARDING.md](docs/ONBOARDING.md) — developer setup
- [RUNBOOK.md](docs/RUNBOOK.md) — operational runbook
- [API.md](docs/API.md) — API reference
- [CHANGELOG.md](CHANGELOG.md)
