# Changelog — banxe-payment-core

## [Unreleased] — Sprint 9

### Added
- Initial scaffold: ports, adapters, settlement, authorization, compliance_bridge
- `PaymentSwitchPort` ABC + `HyperswitchAdapter` (stub, Sprint 9 deployment)
- `IssuerPort` ABC + `PaymentologyAdapter` (Companion API pattern, Sprint 10)
- `LedgerPort` ABC + `MidazAdapter` (:8095, Sprint 8)
- `AuthEngine`: compliance → balance → approve/decline (ADR-015)
- `ComplianceScreening`: pre-auth sanctions via :8093 (I-01, I-02)
- `IPMParser` stub (Sprint 11) + `SettlementReconciler`
- Hyperswitch Docker Compose stack (:8096, :8097, :8098)
- `.semgrep/rules.yaml`: I-05 (float-money), PAN, screening-first, no-AGPLv3
- CI workflow: ruff + bandit + semgrep + pytest (≥80% coverage)
- ADR-015 in banxe-architecture + SERVICE-MAP updated
