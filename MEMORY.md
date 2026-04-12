# MEMORY.md — banxe-payment-core Project State

> Last updated: 2026-04-12 | Commit: 4441673

---

## ✅ Project Status: SPRINT 9 COMPLETE

**GitHub:** https://github.com/CarmiBanxe/banxe-payment-core  
**HEAD commit:** `4441673`

---

## Quality Gates — All Passed

| Check | Status | Detail |
|---|---|---|
| Tests | ✅ | 67 tests, 81% coverage (threshold: 80%) |
| Linting | ✅ | ruff — clean |
| Security | ✅ | bandit — clean |
| Semgrep rules | ✅ | I-05, I-10, I-15, I-01 enforced statically |
| README | ✅ | Present |
| AGENTS.md | ✅ | Present |
| .claude/CLAUDE.md | ✅ | Present |
| .pre-commit-config.yaml | ✅ | ruff + bandit |
| CI workflow | ✅ | .github/workflows/ci.yml |
| .gitignore | ✅ | No __pycache__ / build artifacts |

---

## Sprint 9 — Deliverables

| Deliverable | Status | Commit |
|---|---|---|
| ADR-015 authored (banxe-architecture) | ✅ | a5313c3 |
| SERVICE-MAP updated: ports 8096/8097/8098 | ✅ | a5313c3 |
| `PaymentSwitchPort` ABC | ✅ | 43849be |
| `IssuerPort` ABC | ✅ | 43849be |
| `LedgerPort` ABC | ✅ | 43849be |
| `HyperswitchAdapter` (stub, I-10 guard) | ✅ | 43849be |
| `PaymentologyAdapter` (stub, Sprint 10) | ✅ | 43849be |
| `MidazAdapter` (:8095) | ✅ | 43849be |
| `AuthEngine`: I-01 → I-02 → I-04 → balance → approve | ✅ | 43849be |
| `ComplianceScreening`: Category A + fail-closed | ✅ | 43849be |
| `IPMParser` stub (Sprint 11) | ✅ | 43849be |
| `SettlementReconciler` | ✅ | 43849be |
| Hyperswitch Docker Compose deploy docs | ✅ | 4441673 |
| `.semgrep/rules.yaml` (I-05, I-10, I-15, I-01) | ✅ | 43849be |
| Full test suite: 67 tests, 81% coverage | ✅ | cb369c5 |
| Repo health: all 5 criteria | ✅ | cb369c5 |

### Hyperswitch Deploy (GMKtec)

```bash
git clone https://github.com/juspay/hyperswitch
# Port remapping (ADR-015 / SERVICE-MAP):
sed -i 's/"5432:5432"/"5434:5432"/g' docker-compose.yml
sed -i 's/"8080:8080"/"8096:8080"/g' docker-compose.yml
sed -i 's/"6379:6379"/"6380:6379"/g' docker-compose.yml
sed -i 's/"9000:8080"/"8097:8080"/g' docker-compose.yml
docker compose up -d
```

| Service | Port |
|---|---|
| Hyperswitch App Server | :8096 |
| Control Center | :8097 |
| Web SDK | :9050 |
| Postgres (internal) | :5434 |
| Redis (internal) | :6380 |

---

## Architecture (ADR-015)

### Components

| Component | Role | Port |
|---|---|---|
| `AuthEngine` | Compliance-first: I-01 → I-02 → I-04 → balance → approve/decline/refer | — |
| `ComplianceScreening` | Category A + fail-closed sanctions check | `:8093` |
| `HyperswitchAdapter` | `PaymentSwitchPort` — payment switch (acquiring/routing) | `:8096` |
| `PaymentologyAdapter` | `IssuerPort` + Companion API — card issuing | — |
| `MidazAdapter` | `LedgerPort` — double-entry ledger | `:8095` |
| `IPMParser` | ISO 8583 / T112 settlement file parser | — |
| `SettlementReconciler` | Reconciles IPM records vs Midaz balances | — |

### Auth Flow (I-series invariants)

```
ComplianceScreening (I-01/I-02) → HITL threshold ≥£10k (I-04)
    → Midaz balance check → APPROVE / DECLINE / REFER
```

---

## Sprint Roadmap

| Sprint | Focus | Status |
|---|---|---|
| 1–8 | Infrastructure, Midaz ledger, compliance API, banxe-emi-stack | ✅ Done |
| **9** | **ADR-015 + full payment-core scaffold + Hyperswitch deploy docs** | ✅ **Done** |
| 10 | Paymentology Companion API live, AuthEngine → card authorisations end-to-end | 🔜 Next |
| 11 | IPMParser (ISO 8583 full parse) + daily settlement run | 🔜 Planned |
| 12 | ClickHouse decision log (I-24), monitoring, production hardening | 🔜 Planned |

---

## Key Decisions

- **Fail-closed compliance**: `ComplianceScreening` → BLOCK on any API error (I-01)
- **Category A static list**: `_CATEGORY_A = frozenset(["RU","BY","IR","KP","CU","MM","AF"])` — no API call needed (I-02)
- **HITL threshold**: `1_000_000` minor units = £10,000 → REFER decision (I-04)
- **Amounts always int**: enforced by `_CATEGORY_A` semgrep rule + dataclass types (I-05)
- **No fake integrations**: `NotImplementedError` if env vars missing (I-10)
- **No AGPLv3**: Hyperswitch = Apache 2.0 ✅ (I-15)
- **Port/Adapter**: every external system behind an ABC — swappable without touching AuthEngine (I-20)
- **Coverage threshold**: 80% in CI — currently 81%
