# Quality Gate — banxe-payment-core

## Standards

| Check | Tool | Threshold | Status |
|-------|------|-----------|--------|
| Lint | Ruff | 0 errors | `make lint` |
| Format | Ruff format | Compliant | `make lint` |
| Security | Bandit | 0 HIGH/MEDIUM | `make lint` |
| SAST | Semgrep | 0 BANXE violations | `make semgrep` |
| Tests | pytest | ≥80% coverage | `make test` |
| Pre-commit | gitleaks + ruff | Green | `pre-commit run` |

## Run Quality Gate

```bash
make quality-gate
```

## Invariant Coverage

| Invariant | Test | Location |
|-----------|------|---------|
| I-01 (sanctions first) | `test_compliance_checked_before_balance` | test_authorization.py |
| I-02 (Category A) | `test_block_on_compliance_screening` | test_authorization.py |
| I-04 (HITL ≥£10k) | `test_refer_on_hitl_threshold` | test_authorization.py |
| I-05 (int amounts) | `test_amount_minor_is_int` | test_ports.py |
| I-20 (abstract ports) | `test_cannot_instantiate_abstract` | test_ports.py |
