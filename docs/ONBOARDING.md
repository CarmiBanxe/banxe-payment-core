# Onboarding — banxe-payment-core

## Prerequisites

- Python 3.11+
- Docker + Docker Compose (for Hyperswitch stack)
- Midaz running at :8095 (Sprint 8)
- Compliance API running at :8093 (banxe-emi-stack)

## Setup

```bash
cd ~/banxe-payment-core
pip install -e ".[dev]"
cp .env.example .env
# Fill in .env with your values
```

## Running tests

```bash
make test          # pytest + coverage
make quality-gate  # full gate
```

## Hyperswitch (Sprint 9)

```bash
docker compose -f docker/docker-compose.yml up -d
# Wait for healthy:
docker compose -f docker/docker-compose.yml ps
# App: http://localhost:8096/health
# UI:  http://localhost:8097
```

## Key files

| File | Purpose |
|------|---------|
| `src/ports/` | Abstract interfaces (never import adapters directly) |
| `src/adapters/` | Concrete implementations (Hyperswitch, Paymentology, Midaz) |
| `src/authorization/auth_engine.py` | Core approve/decline logic |
| `src/compliance_bridge/screening.py` | Pre-auth sanctions check |
| `src/settlement/` | IPM parser + reconciliation (Sprint 11) |
| `.claude/CLAUDE.md` | AI agent context |
