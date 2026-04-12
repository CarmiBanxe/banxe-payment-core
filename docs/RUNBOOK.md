# Runbook — banxe-payment-core

## Health Checks

```bash
# Hyperswitch
curl http://localhost:8096/health

# Midaz
curl http://localhost:8095/v1/health

# Compliance API
curl http://localhost:8093/health
```

## Incidents

### Payment authorization failing
1. Check compliance API: `curl :8093/health`
2. Check Midaz: `curl :8095/v1/health`
3. Check Hyperswitch: `curl :8096/health`
4. Review ClickHouse audit log for decision events (I-24)

### Settlement mismatch
1. Run reconciler manually against latest IPM file
2. Flag discrepancies to MLRO (HITL — I-04)
3. Do NOT auto-correct — append to audit log first (I-24)

### Card authorisation blocked unexpectedly
1. Check ComplianceScreening response (`/check_transaction`)
2. Verify counterparty country is not Category A (I-02)
3. Check amount < £10,000 threshold (I-04)
4. Escalate to MLRO if pattern suggests false positive

## Docker

```bash
# Start
docker compose -f docker/docker-compose.yml up -d

# Stop
docker compose -f docker/docker-compose.yml down

# Logs
docker logs banxe-hyperswitch-app --tail 100 -f
```
