# CLAUDE.md — banxe-payment-core

## Проект

Payment Processing Stack для BANXE AI Bank / TOMPAY UK EMI (Principal Member Mastercard).

**ADR-015:** Hyperswitch (switch) + Paymentology (issuer) + Midaz (ledger)

## Архитектура

- Adapter pattern: `PaymentSwitchPort`, `IssuerPort`, `LedgerPort`
- Settlement: собственный Mastercard IPM parser (ядро, незаменяемое)
- Compliance bridge: pre-auth sanctions screening → `:8093`
- Authorization: balance check via Midaz → approve/decline

## Инварианты (из banxe-architecture/INVARIANTS.md)

| ID | Rule |
|----|------|
| I-01 | Sanctions screening FIRST, before any payment authorization |
| I-02 | Category A jurisdictions → REJECT (RU/BY/IR/KP/CU/MM/AF) |
| I-04 | ≥£10,000 → EDD + HITL |
| I-10 | Нет фейковых интеграций — если Paymentology sandbox не подключен, не генерировать данные |
| I-12 | Validators = source of truth |
| I-15 | НЕ использовать AGPLv3 компоненты (Hyperswitch = Apache 2.0 ✅) |
| I-20 | Каждый контур заменяем независимо |
| I-24 | Decision Event Log = append-only |
| I-28 | Instruction Ledger Discipline |
| I-29 | Documentation Standard (CHANGELOG, ONBOARDING, RUNBOOK, API, QUALITY) |

## Порты

| Сервис | Порт | Статус |
|--------|------|--------|
| Hyperswitch App | 8096 | PLANNED Sprint 9 |
| Hyperswitch Control Center | 8097 | PLANNED Sprint 9 |
| Hyperswitch Card Vault | 8098 | PLANNED Sprint 9 |
| Midaz Ledger | 8095 | ✅ Sprint 8 |
| Compliance API | 8093 | ✅ |

## Стек

- Python 3.11+, FastAPI
- Ruff, Bandit, Semgrep
- pytest (minimum 80% coverage)
- Docker Compose (Hyperswitch stack)
- PostgreSQL (Hyperswitch internal), ClickHouse (audit)

## Зависимости

- `banxe-architecture` (canon, INVARIANTS.md, ADR-015)
- `banxe-emi-stack` (compliance screening `:8093`)
- Midaz (ledger `:8095`, уже Sprint 8)

## Качество (make quality-gate)

```bash
make lint       # ruff check + ruff format --check + bandit
make test       # pytest --cov=src --cov-fail-under=80
make semgrep    # semgrep .semgrep/rules.yaml
make quality-gate  # lint + test + semgrep
```
