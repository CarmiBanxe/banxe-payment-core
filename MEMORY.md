# MEMORY.md — banxe-payment-core Project State

> Last updated: 2026-04-12 | Commit: cb369c5

---

## ✅ Project Status: COMPLETE (Foundation Phase)

**GitHub:** https://github.com/CarmiBanxe/banxe-payment-core  
**HEAD commit:** `cb369c5`

---

## Quality Gates — All Passed

| Check | Status | Detail |
|---|---|---|
| Tests | ✅ | 67 tests, 81% coverage (threshold: 80%) |
| Linting | ✅ | ruff — clean |
| Security | ✅ | bandit — clean |
| README | ✅ | Present |
| AGENTS.md | ✅ | Present |
| .claude/CLAUDE.md | ✅ | Present |
| .pre-commit-config.yaml | ✅ | Present |
| CI workflows | ✅ | Present |
| .gitignore | ✅ | No __pycache__ / .coverage |

---

## Architecture (ADR-015)

### Implemented Components

| Component | Role | Port / Sprint |
|---|---|---|
| `AuthEngine` | Compliance-first auth flow: I-01 → I-02 → I-04 → balance check → approve | — |
| `ComplianceScreening` | Category A screening; fail-closed on error | `:8093` |
| `HyperswitchAdapter` | `PaymentSwitchPort` — card acquiring routing | `:8096` (Sprint 9) |
| `PaymentologyAdapter` | `IssuerPort` + Companion API — card issuing | — (Sprint 10) |
| `MidazAdapter` | `LedgerPort` — double-entry ledger (Midaz) | `:8095` |
| `IPMParser` | ISO 8583 Interchange Parser | — (Sprint 11) |
| `SettlementReconciler` | Settlement reconciliation engine | — (Sprint 11) |

### Auth Flow (I-series)
```
I-01 (Identity) → I-02 (KYC) → I-04 (Sanctions/AML) → Balance Check → Approve
```

---

## Sprint Roadmap

| Sprint | Focus | Status |
|---|---|---|
| 1–8 | Foundation, AuthEngine, ComplianceScreening, CI/CD | ✅ Done |
| 9 | HyperswitchAdapter (PaymentSwitchPort `:8096`) | 🔜 Next |
| 10 | PaymentologyAdapter (IssuerPort, Companion API) | 🔜 Planned |
| 11 | IPMParser + SettlementReconciler (ISO 8583) | 🔜 Planned |

---

## Key Decisions

- **Fail-closed compliance**: ComplianceScreening returns DENY on any error (no silent pass-through)
- **Port/Adapter pattern**: All external integrations behind interfaces (PaymentSwitchPort, IssuerPort, LedgerPort)
- **Coverage threshold**: 80% enforced in CI
- **Pre-commit hooks**: ruff + bandit run locally before every push
