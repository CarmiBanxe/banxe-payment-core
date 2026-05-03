## Рекомендации автоматизаций для banxe-payment-core

**Профиль проекта:** Python 3.11 / FastAPI / Pydantic v2 · Ruff + Bandit + Semgrep (с BANXE-кастомными правилами I-01/I-05) · pytest 80%+ · Adapter pattern (Hyperswitch/Paymentology/Midaz) · FCA-regulated payments · Docker Compose stack.

**Существующее:** `settings.json` со слэш-командами и `deny_paths`, pre-commit только с gitleaks, Makefile c quality-gate. Главные пробелы — нет PostToolUse автоформата, нет subagent для проверки инвариантов, нет hook-enforcement для `deny_paths`.

---

### ⚡ Hooks (top 2)

#### 1. `PostToolUse` → ruff format + check на Python-файлах
**Зачем:** Сейчас форматирование/линт ловятся только в `make lint` или CI. Auto-fix на каждом Edit/Write убирает циклы «отредактировал → CI красный → переоткрыл».
**Где:** `.claude/settings.json`
```json
"hooks": {
  "PostToolUse": [{
    "matcher": "Edit|Write",
    "hooks": [{
      "type": "command",
      "command": "if [[ \"$CLAUDE_FILE_PATHS\" == *.py ]]; then ruff format $CLAUDE_FILE_PATHS && ruff check --fix $CLAUDE_FILE_PATHS; fi"
    }]
  }]
}
```

#### 2. `PreToolUse` → блокировка edit в чувствительных путях + semgrep I-01/I-05 на трогаемых файлах
**Зачем:** `deny_paths` в `settings.json` декларативный — без hook это не enforced. Также при правке `src/authorization/` или `src/compliance_bridge/` имеет смысл прогонять `.semgrep/rules.yaml` (banxe-float-money, banxe-screening-first, banxe-no-raw-pan) до того, как diff окажется в коммите.

---

### 🤖 Subagents (top 2)

#### 1. `invariant-reviewer` — аудит I-01/I-02/I-04/I-05/I-10/I-15
**Зачем:** Инварианты из CLAUDE.md — это ровно то, что должен ловить ревьюер на каждом PR (screening first, no float for money, no raw PAN, no AGPLv3, ≥£10k → EDD/HITL). Сейчас это контролирует semgrep частично + человек. Subagent параллелит проверку всех инвариантов и возвращает прямые ссылки на нарушения.
**Где:** `.claude/agents/invariant-reviewer.md`
**Тулы:** Read, Grep, Glob, Bash (для `make semgrep`).

#### 2. `payments-security-reviewer` — PCI/PAN/секреты/Mastercard IPM
**Зачем:** Settlement-парсер Mastercard IPM — собственное ядро, его правки требуют security-фокуса (PAN handling, BIN/PAN exposure в логах, decimal arithmetic в clearing файлах, decision log append-only I-24). Bandit/semgrep — статика; subagent смотрит контекст.

---

### 🎯 Skills (top 2)

#### 1. `adapter-scaffold` — генератор нового адаптера под `PaymentSwitchPort`/`IssuerPort`/`LedgerPort`
**Зачем:** I-20 «каждый контур заменяем независимо» => адаптеры будут добавляться (новый switch/issuer/ledger). Skill с шаблоном `src/adapters/<name>_adapter.py` + парный stub в `tests/test_adapters.py` экономит ручную работу и гарантирует, что все методы порта реализованы.
**Где:** `.claude/skills/adapter-scaffold/SKILL.md` (user-invocable: `/adapter-scaffold <name> <port>`).

#### 2. `decision-log-emit` — корректная запись append-only Decision Event Log (I-24)
**Зачем:** Любой authorization/screening путь должен писать в DecisionEventLog в одном формате. Skill инкапсулирует схему события (timestamp, actor, decision, evidence_hash) и предохраняет от случайных мутаций существующих записей.
**Где:** `.claude/skills/decision-log-emit/SKILL.md` (Both — Claude и юзер вызывают).

---

### 🔌 MCP servers (top 2)

#### 1. **context7** — live docs для FastAPI / Pydantic v2 / Hyperswitch / httpx
**Зачем:** Pydantic v2 API часто меняется (validators, model_config), Hyperswitch API растёт по Sprint 9 — context7 даёт актуальные сигнатуры без устаревших примеров из обучающей выборки.
**Установка:** `claude mcp add context7`

#### 2. **PostgreSQL MCP** — для Hyperswitch internal Postgres + ClickHouse audit
**Зачем:** Settlement reconciliation и authorization-debug требуют ad-hoc запросов к Hyperswitch БД и ClickHouse audit. MCP даёт schema-aware запросы прямо из ассистента, без переключения на psql.
**Установка:** `claude mcp add postgres --env DATABASE_URL=...` (read-only role обязательно).

---

**Гэп №1, который вижу сразу:** `.pre-commit-config.yaml` содержит только gitleaks — `ruff`, `bandit`, `semgrep` живут только в Makefile/CI. Добавить их в pre-commit — самая дешёвая локальная гарантия quality-gate (это не automation Claude, но просится в одну линию с hook #1).

Скажи, если нужно развернуть какую-то из позиций (готов написать конфиг/agent.md/SKILL.md), или показать ещё варианты по конкретной категории.
