# Рекомендуемые MCP-серверы для `banxe-payment-core`

Текущее состояние: подключён только Notion. Atlassian/Google нуждаются в авторизации, `ruflo` падает.

## Уже настроено — нужно решить
| Сервер | Статус | Действие |
|--------|--------|----------|
| **Notion** | ✓ | Держать (canon docs, ADR-015, INVARIANTS) |
| **Atlassian Rovo** | ! auth | **Авторизовать** — Jira tickets `IL-XXX` из commit format |
| **Google Drive** | ! auth | Авторизовать только если там лежат compliance-докум. Иначе отключить |
| **Gmail / Calendar** | ! auth | **Отключить** — нерелевантно payment-core |
| **ruflo** | ✗ fail | Удалить или починить SSH |

## Стоит добавить (приоритет для FCA EMI core)

| Сервер | Зачем | Приоритет |
|--------|-------|-----------|
| **filesystem** (`@modelcontextprotocol/server-filesystem`) | Доступ к `~/banxe-architecture` (canon, INVARIANTS) и `~/banxe-emi-stack` без переключения cwd | 🔴 high |
| **postgres** (`@modelcontextprotocol/server-postgres`) | Hyperswitch internal DB (`:8096`) + Midaz (`:8095`) — read-only схема для отладки | 🔴 high |
| **github** (`@modelcontextprotocol/server-github`) | PR review, CI status, issues `IL-XXX` | 🟡 med |
| **sentry** или эквивалент | Production errors из Hyperswitch/Compliance bridge | 🟡 med |
| **fetch** (`@modelcontextprotocol/server-fetch`) | Hyperswitch/Midaz/Compliance API live-проверки `:8093/:8095/:8096-8098` | 🟢 low |

## НЕ добавлять
- ❌ Любые MCP с хостингом в RU/BY/IR/KP/CU/MM/AF (I-02)
- ❌ AGPLv3-компоненты (I-15)
- ❌ Серверы, которые отправляют код/секреты во внешние LLM без TLS-pinning (PCI scope)

## Конкретный шаг сейчас

Файл `/home/mmber/banxe/banxe-payment-core/.claude/recos-mcp.md` пустой. Заполнить его этим списком?
