# MEMORY.md — banxe-payment-core Project State

> Last updated: 2026-04-13 | Commit: a166d1e

---

## ✅ Project Status: SPRINT 10 COMPLETE

**GitHub:** https://github.com/CarmiBanxe/banxe-payment-core  
**HEAD commit:** `a166d1e`

---

## Quality Gates — All Passed

| Check | Status | Detail |
|---|---|---|
| Tests | ✅ | 157 tests, 86.17% coverage (threshold: 80%) |
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

## Sprint 10 — Deliverables (GMKtec merge)

| Deliverable | Status | Commit |
|---|---|---|
| Paymentology Companion API (`src/paymentology/`) | ✅ | 6732598 |
| `adapter.py` — issue_card, get_balance, authorise_transaction | ✅ | 6732598 |
| `xmlrpc_builder.py` — XML-RPC serialization | ✅ | 6732598 |
| `checksum.py` — MD5 HMAC for Paymentology API | ✅ | 6732598 |
| `remote_handler.py` — Remote API callback handler | ✅ | 6732598 |
| `webhook_server.py` — FastAPI webhook `/paymentology/webhook` | ✅ | 6732598 |
| IPMParser full implementation (`src/settlement/ipm_parser.py`) | ✅ | bf4e8a8 |
| SettlementReconciler (`src/settlement/reconciler.py`) | ✅ | bf4e8a8 |
| Hyperswitch Docker stack — ALL 4 containers UP | ✅ | cd767cf |
| Coverage fix: 157 tests, 86.17% | ✅ | a166d1e |

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
| `PaymentologyAdapter` (stub → wired Sprint 11) | ✅ | 43849be |
| `MidazAdapter` (:8095) | ✅ | 43849be |
| `AuthEngine`: I-01 → I-02 → I-04 → balance → approve | ✅ | 43849be |
| `ComplianceScreening`: Category A + fail-closed | ✅ | 43849be |
| `SettlementReconciler` | ✅ | 43849be |
| Hyperswitch Docker Compose deploy | ✅ | cd767cf |
| `.semgrep/rules.yaml` (I-05, I-10, I-15, I-01) | ✅ | 43849be |

---

## Full Service Stack (GMKtec — ALL RUNNING as of 2026-04-13)

| Service | Container / Process | Port | Status |
|---|---|---|---|
| Compliance API | uvicorn (banxe-emi-stack) | :8093 | ✅ Up |
| Midaz Ledger | `midaz-ledger` | :8095 | ✅ Up |
| Hyperswitch App | `banxe-hyperswitch-app` | :8096 | ✅ Up |
| Hyperswitch Control Center | `banxe-hyperswitch-ui` | :8097 | ✅ Up |
| Hyperswitch Card Vault | `banxe-hyperswitch-vault` | :8098 | ✅ Up |
| Hyperswitch Postgres | `banxe-hyperswitch-pg` | internal | ✅ Healthy |
| Midaz MongoDB | `midaz-mongodb` | :5703 (internal) | ✅ Healthy |
| Midaz RabbitMQ | `midaz-rabbitmq` | :3003/:3004 | ✅ Healthy |

### Start commands

```bash
# Compliance API (:8093)
cd /home/mmber/banxe-emi-stack
source /opt/banxe/compliance/venv/bin/activate
nohup uvicorn api.main:app --host 0.0.0.0 --port 8093 --workers 1 > /tmp/compliance-api.log 2>&1 &

# Midaz (:8095)
cd /home/mmber/vibe-coding
docker compose -f docker-compose.midaz.yml up -d

# Hyperswitch stack (:8096/:8097/:8098)
cd /home/mmber/banxe-payment-core/docker
docker compose up -d
```

### Key fixes applied
- Hyperswitch: `[redis_settings]` → `[redis]` in sandbox.toml (official juspay section name)
- Hyperswitch Vault: reads env vars only; needs `LOCKER__TENANT_SECRETS__PUBLIC__*`
- Midaz: host gateway IP was `172.22.0.1` → corrected to `172.20.0.1` (actual midaz-network gateway)
- Midaz: MongoDB keyfile created via `docker run mongo:8` to get uid 999 ownership
- Midaz: keyfile path updated from `/data/banxe/midaz/mongo/` → `/home/mmber/midaz-data/mongo/`
- Postgres: `midaz_app` user + `midaz_onboarding` + `midaz_transaction` DBs created in banxe-postgres

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
| `IPMParser` | ISO 8583 / T112 settlement file parser (Sprint 10 full impl) | — |
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
| 9 | ADR-015 + full payment-core scaffold + Hyperswitch deploy docs | ✅ Done |
| **10** | **Paymentology Companion API + Hyperswitch stack UP + IPMParser full** | ✅ **Done** |
| 11 | Wire PaymentologyAdapter → src/paymentology; AuthEngine → Midaz live | 🔜 Next |
| 12 | ClickHouse decision log (I-24), monitoring, production hardening | 🔜 Planned |

---

## Pending (Sprint 11)

- [ ] Wire `PaymentologyAdapter` → `src/paymentology/adapter.py` (currently stub)
- [ ] Bring up Midaz :8095 → enable `MidazAdapter` live calls
- [ ] Bring up Compliance API :8093
- [ ] ADR-015 status: PROPOSED → ACCEPTED
- [ ] ADR-014 status: PROPOSED → ACCEPTED
- [ ] Create `docs/MIGRATION-TRIBE-TO-COMPOSABLE.md`

---

## Key Decisions

- **Fail-closed compliance**: `ComplianceScreening` → BLOCK on any API error (I-01)
- **Category A static list**: `_CATEGORY_A = frozenset(["RU","BY","IR","KP","CU","MM","AF"])` — no API call needed (I-02)
- **HITL threshold**: `1_000_000` minor units = £10,000 → REFER decision (I-04)
- **Amounts always int**: enforced by semgrep rule + dataclass types (I-05)
- **No fake integrations**: `NotImplementedError` if env vars missing (I-10)
- **No AGPLv3**: Hyperswitch = Apache 2.0 ✅ (I-15)
- **Port/Adapter**: every external system behind an ABC — swappable without touching AuthEngine (I-20)
- **Coverage threshold**: 80% in CI — currently 86.17%
- **[redis] not [redis_settings]**: official juspay section name; Config-rs silently ignores unknown sections
