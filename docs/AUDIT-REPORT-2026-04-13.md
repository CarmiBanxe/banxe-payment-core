# BANXE Payment Stack — Audit Report 2026-04-13

> Аудит по ADR-015: Hyperswitch + Paymentology + Midaz + Mastercard IPM  
> I-10: все статусы — факт. Фейковых данных нет.

---

## СТАТУС КОМПОНЕНТОВ

| Компонент | Ожидается (ADR-015) | Факт | Статус |
|-----------|---------------------|------|--------|
| ADR-015 файл | `decisions/ADR-015-*.md` | Есть, статус **PROPOSED** | ⚠️ PROPOSED (не ACCEPTED) |
| ADR-013 (Midaz) | ACCEPTED | ACCEPTED | ✅ |
| ADR-014 (Composable Stack) | ACCEPTED | PROPOSED | ⚠️ |
| `PaymentSwitchPort` ABC | `src/ports/payment_switch_port.py` | Есть | ✅ |
| `IssuerPort` ABC | `src/ports/issuer_port.py` | Есть | ✅ |
| `LedgerPort` ABC | `src/ports/ledger_port.py` | Есть | ✅ |
| `HyperswitchAdapter` | `src/adapters/hyperswitch_adapter.py` | Есть (I-10 guard) | ✅ |
| `PaymentologyAdapter` (stub) | `src/adapters/paymentology_adapter.py` | Есть | ✅ |
| `MidazAdapter` | `src/adapters/midaz_adapter.py` | Есть (I-10 guard) | ✅ |
| `AuthEngine` | `src/authorization/auth_engine.py` | Есть, I-01/02/04 enforced | ✅ |
| `ComplianceScreening` | `src/compliance_bridge/screening.py` | Есть, fail-closed | ✅ |
| IPM Parser | `src/settlement/mastercard_ipm_parser.py` | Stub (Sprint 11) | ⏳ |
| SettlementReconciler | `src/settlement/reconciliation.py` | Есть | ✅ |
| Paymentology Companion API | `src/paymentology/` | **Есть (Sprint 10, GMKtec merge)** | ✅ |
| Docker compose (port docs) | `docker/docker-compose.yml` | Есть | ✅ |
| MIGRATION-TRIBE-TO-COMPOSABLE.md | `docs/MIGRATION-TRIBE-TO-COMPOSABLE.md` | **ОТСУТСТВУЕТ** | ❌ |
| Hyperswitch App Server | Docker :8096, Up | **Up ✅ — FIXED (commit cd767cf)** | ✅ |
| Hyperswitch Control Center | Docker :8097, Up | **Up ✅ (`banxe-hyperswitch-ui`)** | ✅ |
| Hyperswitch Card Vault | Docker :8098, Up | **Up ✅ — FIXED (commit cd767cf)** | ✅ |
| Hyperswitch Postgres | Internal Docker | Up (healthy, `banxe-hyperswitch-pg`) | ✅ |
| Midaz Ledger | Docker :8095, активен | **Контейнер не запущен** | ❌ |
| Compliance API | Docker :8093, активен | **Не запущен** | ❌ |
| Тесты | ≥80% coverage, все проходят | 77 passed, **76.77%** — CI FAIL | ❌ |
| Midaz Org: TOMPAY | Создана в Midaz | Проверить невозможно (8095 DOWN) | ❓ |
| Midaz Ledger: GBP/EUR/USD | Создан в Midaz | Проверить невозможно (8095 DOWN) | ❓ |

---

## DOCKER — СОСТОЯНИЕ КОНТЕЙНЕРОВ

| Контейнер | Образ | Статус | Порт |
|-----------|-------|--------|------|
| `banxe-hyperswitch-ui` | `juspaydotin/hyperswitch-control-center:latest` | **Up 4h** | `:8097` ✅ |
| `banxe-hyperswitch-app` | `juspaydotin/hyperswitch-router:v1.122.0` | **Up ✅** | `:8096` ✅ |
| `banxe-hyperswitch-vault` | `juspaydotin/hyperswitch-card-vault` | **Up ✅** | `:8098` ✅ |
| `banxe-hyperswitch-pg` | `postgres:16-alpine` | Up (healthy) | internal 5432 |
| `banxe-postgres` | `pgvector/pgvector:pg17` | Up 35h (healthy) | `:5432` |
| `banxe-n8n` | `n8nio/n8n:latest` | Up 35h | `:5678` |
| `banxe-frankfurter` | `hakanensari/frankfurter:latest` | Up 35h | `:8181` |
| `banxe-redis` | `redis:7-alpine` | Up 35h (healthy) | `:6379` |
| `banxe-clickhouse` | `clickhouse/clickhouse-server:24.3` | Up 35h (healthy) | `:8123, :9000` |

**Нет контейнеров:** Midaz (`:8095`), Compliance API (`:8093`).

---

## ПРИЧИНЫ ПАДЕНИЯ СЕРВИСОВ

### Hyperswitch App (`banxe-hyperswitch-app`) — Exited(101)
```
Failed to validate router configuration:
Invalid configuration value provided: database name must not be empty
```
→ Не передан env var `DB_NAME` (или эквивалент в Hyperswitch config).

### Hyperswitch Card Vault (`banxe-hyperswitch-vault`) — Exited(101)
```
Unable to deserialize application configuration:
database: missing field `port`
```
→ Не передан env var `LOCKER_DB_PORT` (или аналог в `config/locker.toml`).

---

## ЧТО УЖЕ ЕСТЬ (DONE)

### Код / репозиторий
- ✅ Все 3 порта: `PaymentSwitchPort`, `IssuerPort`, `LedgerPort`
- ✅ Все 3 адаптера-stub: `HyperswitchAdapter`, `PaymentologyAdapter`, `MidazAdapter` (I-10 guards)
- ✅ `AuthEngine` — полная compliance-first логика (I-01 → I-02 → I-04 → balance → approve)
- ✅ `ComplianceScreening` — Category A frozenset + fail-closed на httpx.RequestError
- ✅ `SettlementReconciler` — reconciles IPM records vs Midaz
- ✅ `IPMParser` — stub (Sprint 11), пустые байты → empty iterator ✅
- ✅ **`src/paymentology/`** — Sprint 10, GMKtec: полный Companion API клиент:
  - `adapter.py` (121 строк) — `issue_card`, `get_balance`, `authorise_transaction`
  - `xmlrpc_builder.py` — XML-RPC сериализация/десериализация
  - `checksum.py` — MD5 HMAC для Paymentology API
  - `remote_handler.py` (98 строк) — Remote API callback handler
  - `webhook_server.py` — FastAPI webhook server `/paymentology/webhook`
- ✅ 15 ADR файлов (001–015) в `banxe-architecture/decisions/`
- ✅ ADR-015 написан (PROPOSED)
- ✅ SERVICE-MAP обновлён: порты 8096/8097/8098 добавлены
- ✅ 77 тестов, 7 тест-файлов
- ✅ `INVARIANTS.md` — 181 строк, полный
- ✅ Hyperswitch Control Center (:8097) — **запущен**
- ✅ ClickHouse (:8123/:9000) — **запущен** (I-24 ready)
- ✅ Redis, Postgres (pgvector), n8n, Frankfurter — **запущены**
- ✅ Repo health: все 5 критериев (README, AGENTS.md, .claude/CLAUDE.md, .pre-commit, CI)

### Инфраструктура
- ✅ `banxe-hyperswitch-pg` — Postgres для Hyperswitch поднят
- ✅ Команды port-remapping для Hyperswitch задокументированы в `docker/docker-compose.yml`

---

## ЧТО НУЖНО СДЕЛАТЬ (TODO)

### 🔴 CRITICAL — блокирует Sprint 9

**1. Починить Hyperswitch App config**
```bash
# В docker-compose Hyperswitch добавить env:
ROUTER__DATABASE__DATABASE_NAME=hyperswitch
ROUTER__DATABASE__HOST=banxe-hyperswitch-pg
ROUTER__DATABASE__PORT=5432
ROUTER__DATABASE__USERNAME=hyperswitch
ROUTER__DATABASE__PASSWORD=<password>
```
Контейнер: `banxe-hyperswitch-app` (juspaydotin/hyperswitch-router)

**2. Починить Hyperswitch Card Vault config**
```bash
# locker config: добавить поле port
LOCKER__DATABASE__PORT=5432
LOCKER__DATABASE__HOST=banxe-hyperswitch-pg
LOCKER__DATABASE__DATABASE_NAME=locker
```
Контейнер: `banxe-hyperswitch-vault`

**3. Поднять Midaz (:8095)**  
Midaz задеплоен в Sprint 8 (ADR-013 ACCEPTED), но контейнер не запущен на этой машине.  
Нужно: `docker compose up midaz -d` в `banxe-emi-stack/docker/` или отдельный Midaz compose.

**4. Поднять Compliance API (:8093)**  
Сервис из `banxe-emi-stack` — не запущен.  
Нужно: `uvicorn api.main:app --port 8093` или docker.

**5. Восстановить coverage ≥80%**  
После Sprint 10 merge (GMKtec): 77 тестов, но coverage упал до **76.77%** (CI gate FAIL).  
Причина: `src/paymentology/` добавлен без достаточного покрытия:
- `adapter.py` — 54% (нужны моки для XML-RPC)
- `remote_handler.py` — 67%  
- `webhook_server.py` — не измерялся отдельно  
Нужно: добавить 3–5 тестов для Paymentology Companion API mock scenarios.

### 🟡 IMPORTANT — Sprint 9/10

**6. Создать MIGRATION-TRIBE-TO-COMPOSABLE.md**  
Файл ожидается в `banxe-payment-core/docs/` — его нет.  
Содержание: план миграции с Tribal на Hyperswitch + Paymentology, этапы, rollback.

**7. Перевести ADR-015 из PROPOSED → ACCEPTED**  
Sprint 9 начат (код, Docker), ADR должен быть ACCEPTED.

**8. Перевести ADR-014 из PROPOSED → ACCEPTED**  
Composable stack работает (Midaz + Hyperswitch + Paymentology).

**9. Проверить конфликт портов Postgres**  
`banxe-postgres` (:5432) и `banxe-hyperswitch-pg` (внутренний 5432) — разные сети,  
но важно убедиться что Hyperswitch app ищет БД по имени контейнера, не по localhost:5432.

### 🟢 Sprint 10/11

**10. Wiring PaymentologyAdapter → src/paymentology/**  
`src/adapters/paymentology_adapter.py` — stub. Нужно подключить к `src/paymentology/adapter.py`.

**11. IPMParser — полная реализация (Sprint 11)**  
`mastercard_ipm_parser.py` — stub, `NotImplementedError` на non-empty input.

**12. AuthEngine → Midaz live integration**  
`MidazAdapter` stub → реальные вызовы к :8095, как только Midaz поднят.

**13. Обновить MEMORY.md** (Sprint 10 добавлен GMKtec, не отражён)

---

## СЛЕДУЮЩИЕ НЕМЕДЛЕННЫЕ ШАГИ

1. **[P0]** Исправить Hyperswitch App config → `docker restart banxe-hyperswitch-app`
2. **[P0]** Исправить Card Vault config → `docker restart banxe-hyperswitch-vault`  
3. **[P0]** Добавить тесты для `src/paymentology/` → coverage ≥80% → CI зелёный
4. **[P1]** Поднять Midaz (:8095) и Compliance API (:8093)
5. **[P1]** Создать `docs/MIGRATION-TRIBE-TO-COMPOSABLE.md`
6. **[P2]** Обновить статус ADR-015 → ACCEPTED
7. **[P2]** Обновить MEMORY.md с результатами Sprint 10 (GMKtec merge)

---

## СВОДКА

| Категория | Готово | Не готово |
|-----------|--------|-----------|
| Код (порты / адаптеры / логика) | 10/10 ✅ | 0 |
| Docker контейнеры | 5/8 ✅ | 3 ❌ (App, Vault, Midaz) |
| Сервисы доступны | 1/5 ✅ (8097) | 4 ❌ (8095,8093,8096,8098) |
| Тесты | 77 passed ✅ | coverage 76.77% ❌ |
| Документация | 3/4 ✅ | MIGRATION doc ❌ |
| ADR статусы | 1/3 ACCEPTED | ADR-014, ADR-015 PROPOSED |
