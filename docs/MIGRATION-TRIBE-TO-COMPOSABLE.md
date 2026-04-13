# BANXE - Migration: TRIBE to Composable Payment Architecture

**Status:** ACTIVE  
**Date:** 2026-04-13  
**Author:** Moriel Carmi / BANXE Architecture Team  
**Related:** ADR-015, ADR-013, ADR-014  
**Entity:** TOMPAY UK EMI - Principal Member Mastercard

---

## 1. Problem: Why Leaving TRIBE

**TRIBE Payments** - vendor-locked issuer processing platform. Key limitations:

- Vendor lock-in: monolithic platform, no control over processing/routing/ledger
- High fixed costs: expensive per-transaction processing fees
- No flexibility: cannot customize authorization rules, routing logic, settlement
- Limited control: settlement, reconciliation, disputes controlled by vendor
- Strategic risk: single vendor dependency for critical payment infrastructure

**Decision (ADR-015):** Replace TRIBE with composable open-source + API-first stack.
TOMPAY as Principal Member Mastercard takes full control of payment processing.

---

## 2. Target Architecture: 4 Components

### 2.1 Hyperswitch - Payment Switch (Apache 2.0, Open Source)

Open-source payment orchestrator by Juspay (Rust). 175M txn/day capacity.

Role:
- Single entry point for all payment operations
- Smart routing (rule-based, volume-based, least-cost, auth-rate-based)
- Smart retry and fallback on processor failure
- Payment state machine: authorize -> capture -> void -> refund
- PCI-compliant card vault (token storage, never raw PAN)
- 3DS2 authentication, Mastercard + Visa native support
- Custom connector SDK for Paymentology integration

Ports: App Server :8096 | Control Center :8097 | Card Vault :8098
Replaces from TRIBE: payment routing, transaction lifecycle, retry logic

### 2.2 Paymentology - Issuer Processor (Commercial, Cloud API)

Global cloud issuer processor. Visa/Mastercard/UnionPay. 49 countries.
KEY CHOICE: Companion API - balance stays in OUR Midaz ledger.
BANXE controls all authorization decisions.

Local API (We call Paymentology):
- Card Issuance: CreateLinkedCard, LinkCard, OrderCard, OrderCardWithPinBlock
- Card Lifecycle: ActivateCard, StopCard, UnstopCard, RetireCard, Status
- Card Details: GetCardDetails, GetActiveLinkedCards, GetLinkedCards
- PIN/Security: ChangePin, ResetPin, Set3dSecureCode, UpdateCVV
- Tokenization: ActivateToken, DeleteToken, ListAllTokens, StopToken, etc.

Remote API (Paymentology calls us - MUST IMPLEMENT):
- Balance - check card balance in Midaz
- Deduct - deduct funds on every purchase (CRITICAL real-time path)
- DeductReversal, DeductAdjustment
- LoadAuth, LoadAuthReversal, LoadReversal, LoadAdjustment
- Stop, AdministrativeMessage, ValidatePIN

Secure API: RSA-2048 + AES-256-GCM for PAN/CVV2/PIN encryption

Fallback: CLOWD9 - cloud-native, microservices, Visa+Mastercard certified
Replaces from TRIBE: card issuance, authorization processing, scheme connectivity

### 2.3 Midaz - Core Ledger (Open Source, Lerian)

Domain-driven double-entry ledger platform. Already deployed at :8095.

Structure:
- Organization: TOMPAY UK EMI
- Ledger: Master Ledger (GBP, EUR, USD)
- Assets: GBP, EUR, USD
- Account Types: Client Safeguarding, Operating, Suspense, Fee
- Accounts: Per-customer SVA (Store of Value Accounts)

Critical link: Paymentology Deduct callback -> check balance in Midaz
-> authorize/decline -> record transaction in ledger

Replaces from TRIBE: balance storage, transaction accounting, reconciliation

### 2.4 Mastercard IPM - Settlement (Custom, Core)

Integrated Product Messages - Mastercard clearing via GCMS system.
FORMAT: ISO 8583 (MTI, Bitmaps, Data Elements)

Scope:
- Receive/send clearing files (T112 format)
- Reconciliation: authorizations vs settlement
- Dispute/chargeback management
- FCA CASS 15 reconciliation integration

IMPORTANT: Settlement parser = OWN CORE CODE (irreplaceable, per ADR-011)

Replaces from TRIBE: settlement, reconciliation, dispute management

---

## 3. Transaction Flow

Cardholder swipes card
  |
  v Mastercard/Visa Network
  |
  v Paymentology (Issuer Processor)
  |
  v Remote API callback: Deduct(amount, cardRef)
  |
+-------------------------------+
| banxe-payment-core (FastAPI)  |
| 1. Receive Deduct callback    |
| 2. Sanctions check (:8093)    |
| 3. Midaz: check balance       |
| 4. Midaz: record transaction  |
| 5. Hyperswitch: route/log     |
| 6. Response: APPROVE/DECLINE  |
+-------------------------------+
  |
  v (daily)
Mastercard IPM Parser
  |
  v T112 files -> Reconciliation vs Midaz ledger

---

## 4. Step-by-Step Implementation Plan

### STEP 1: ADR and Architecture Decision (Week 1)

Deliverable: decisions/ADR-015-payment-processing-stack.md in banxe-architecture

Content:
- Context: TOMPAY UK EMI Principal Member Mastercard, replacing TRIBE
- Decision: Hyperswitch (switch) + Midaz (ledger) + Paymentology (issuer processor)
- Justification: replace expensive TRIBE processing with open-source + Principal Member TOMPAY
- Classification (ADR-011):
  * Hyperswitch = Operational Dependency (replaceable) -> PaymentSwitchPort adapter
  * Paymentology/CLOWD9 = Operational Dependency (replaceable) -> IssuerPort adapter
  * Settlement parser = Core (irreplaceable) -> own code
- Approval: CEO (Moriel Carmi) required per banxe-architecture governance
- Update SERVICE-MAP.md: add ports 8096, 8097, 8098

ADR Status: PROPOSED -> CEO approves -> ACCEPTED

### STEP 2: Repository banxe-payment-core (Week 1-2)

Repository structure:

  banxe-payment-core/
  |-- .claude/CLAUDE.md          <- project context (TOMPAY Principal Member)
  |-- .github/workflows/ci.yml   <- lint + test + quality-gate
  |-- .semgrep/rules.yaml
  |-- docker/docker-compose.yml  <- Hyperswitch full stack
  |-- src/
  |   |-- ports/
  |   |   |-- payment_switch_port.py  <- ABC: PaymentSwitchPort
  |   |   |-- issuer_port.py          <- ABC: IssuerPort
  |   |   `-- ledger_port.py          <- ABC: LedgerPort
  |   |-- adapters/
  |   |   |-- hyperswitch_adapter.py  <- PaymentSwitchPort impl
  |   |   |-- paymentology_adapter.py <- IssuerPort impl (Companion API)
  |   |   `-- midaz_adapter.py        <- LedgerPort impl
  |   |-- settlement/
  |   |   |-- mastercard_ipm_parser.py <- IPM/T112 file parser (CORE)
  |   |   `-- reconciliation.py        <- settlement vs ledger reconciliation
  |   |-- authorization/
  |   |   `-- auth_engine.py           <- balance check -> approve/decline
  |   `-- compliance_bridge/
  |       `-- screening.py             <- pre-auth sanctions screening (:8093)
  |-- tests/
  |-- docs/
  |-- pyproject.toml
  `-- Makefile

Adapter pattern (ADR-011 Reference vs Dependency):
- ALL upstream code calls Port interfaces, NEVER direct vendor APIs
- Hyperswitch and Paymentology are REPLACEABLE via port swapping
- Settlement parser is CORE - never replaced

### STEP 3: Deploy Hyperswitch (Week 2-3)

Hyperswitch - open-source payment switch (Rust), Mastercard native support:

1. Docker deploy:
   - app-server (payment processing API)
   - control-center (ops dashboard)
   - card-vault (PCI-compliant token storage)
   - PostgreSQL + Redis

2. Configuration:
   - Create Merchant Account for TOMPAY
   - Write custom connector for Paymentology
     (Hyperswitch has custom connector SDK)
   - Enable Mastercard + Visa in control center
   - Configure smart routing (fallback, retry logic)
   - Activate 3DS2 authentication

Ports: app-server :8096, control-center :8097, card-vault :8098

### STEP 4: Integrate Midaz Ledger (Week 3-4)

Midaz already deployed at :8095 (Sprint 8, ADR-013). Initialize for payments:

1. Create Midaz structure:
   - Organization: TOMPAY UK EMI
   - Ledger: Master Ledger
   - Assets: GBP, EUR, USD
   - Account Types:
     * client_safeguarding (FCA CASS 7 compliant)
     * operating
     * suspense
     * fee
   - Accounts: per-customer SVA accounts

2. Configure Transaction Routes for automatic double-entry

3. Integrate with banxe-emi-stack for FCA CASS 15 reconciliation

4. Event streaming: Midaz ledger events -> compliance monitoring

### STEP 5: Connect Paymentology (Week 4-6)

1. Commercial setup:
   - Sign Paymentology contract (via TOMPAY as Principal Member)
   - Choose Companion API mode (balance in Midaz, not Paymentology)
   - Request Secure API enablement (for PAN/CVV2/PIN encryption)
   - Obtain RSA public key from Paymentology

2. Technical integration:
   - Implement IssuerPort -> PaymentologyAdapter
   - Implement all 13 Remote API webhook endpoints:
     * Balance -> query Midaz
     * Deduct -> check balance, debit Midaz, return APPROVE/DECLINE
     * DeductReversal -> reverse Midaz entry
     * LoadAuth/LoadReversal/etc -> credit Midaz entries
     * Stop/AdministrativeMessage/ValidatePIN -> service handlers
   - Implement Secure API (RSA+AES-256-GCM encryption layer)
   - Sandbox testing: XML Generator + XML Poster tools

3. Card issuance flow:
   - KYC approved (LexisNexis, banxe-lexisnexis-distro)
   - CreateLinkedCard -> physical + virtual cards
   - Apple Pay / Google Pay tokenization via Paymentology

---
### STEP 6: Settlement and Reconciliation (Week 5-7)

1. Build Mastercard IPM/T112 parser:
   - Parse ISO 8583 format files from GCMS
   - Extract MTI, Bitmaps, Data Elements
   - Map to internal transaction model

2. Automated reconciliation pipeline:
   - Mastercard settlement <-> Midaz ledger <-> Paymentology transactions
   - Daily automated run
   - Exception reporting (mismatches, missing entries)

3. FCA integration:
   - Connect to banxe-emi-stack CASS 15 reconciliation
   - Automated FCA reporting from Midaz

### STEP 7: Compliance Stack Integration (Week 6-8)

All compliance checks use existing banxe-emi-stack infrastructure:

1. Pre-authorization flow (MANDATORY, I-01):
   - Sanctions screening FIRST before any payment
   - Category A jurisdictions -> REJECT (RU/BY/IR/KP/CU/MM/AF)
   - Calls banxe-emi-stack compliance API (:8093)

2. Transaction monitoring:
   - Hyperswitch webhook -> banxe-emi-stack AML agent
   - Midaz ledger events -> compliance monitoring
   - Thresholds from STACK-LAYERS.md
   - >= GBP 10,000 -> EDD + HITL (I-04)

3. Card issuance KYC:
   - LexisNexis (banxe-lexisnexis-distro) before CreateLinkedCard
   - Sanctions check on cardholder before issuing

### STEP 8: UI Integration (Week 7-9)

1. banxe-platform/packages/web - dashboard:
   - Account balance (from Midaz via API)
   - Transaction history
   - Card management (block, limits, PIN)

2. banxe-platform/packages/mobile:
   - Mobile card management
   - Apple Pay / Google Pay (via Paymentology tokenization)
   - Push notifications for transactions

3. Hyperswitch Web SDK:
   - Unified checkout for accepting payments

### STEP 9: Testing and Certification (Week 8-12)

1. E2E test scenarios:
   - Card issuance -> activation -> transaction -> authorization -> settlement -> reconciliation
   - Decline flows (insufficient funds, sanctions, blocked card)
   - Reversal and refund flows
   - 3DS authentication flows

2. PCI DSS:
   - Hyperswitch card vault ensures PCI compliance
   - No raw PAN stored in our systems
   - Audit trail (ClickHouse, append-only, I-24)

3. Load testing:
   - Hyperswitch capacity: 175M txn/day
   - Remote API webhook latency: < 200ms (real-time requirement)

4. UAT:
   - Test Mastercard cards (Paymentology sandbox)
   - XML Generator + XML Poster for Paymentology testing

### STEP 10: Production Launch (Week 12-14)

1. Dual-run period (2-4 weeks):
   - Both TRIBE and new stack active
   - Shadow mode: all transactions processed by both
   - Compare results, identify discrepancies

2. Gradual traffic shift:
   - 10% -> 25% -> 50% -> 100% to new stack
   - Rollback plan if issues detected

3. TRIBE decommission:
   - Full cutover to Hyperswitch + Paymentology
   - Terminate TRIBE contract
   - Migrate historical data if needed

4. Monitoring:
   - Grafana dashboards (banxe-infra)
   - Alerts: auth decline rate, latency spikes, reconciliation failures

---

## 5. Economics Comparison

| Item               | TRIBE (current)               | New Stack (Variant B)           |
|--------------------|-------------------------------|---------------------------------|
| Payment processing | High fixed fee                | Hyperswitch - FREE (self-hosted)|
| Issuer processor   | Included in TRIBE             | Paymentology - low per-txn fee  |
| Ledger             | TRIBE proprietary             | Midaz - FREE (open-source)      |
| Vendor lock-in     | HIGH                          | MINIMAL (all replaceable)       |
| Control            | Limited                       | Full (Principal Member + stack) |
| Estimated savings  | Baseline                      | ~60-70% cost reduction          |

---

## 6. Infrastructure Map

| Component                | Port | Technology         | Status              | Role                          |
|--------------------------|------|--------------------|---------------------|-------------------------------|
| Hyperswitch App Server   | 8096 | Rust (Docker)      | PLANNED Sprint 9    | Payment orchestration         |
| Hyperswitch Control Ctr  | 8097 | React (Docker)     | PLANNED Sprint 9    | Payment ops dashboard         |
| Hyperswitch Card Vault   | 8098 | Rust (Docker)      | PLANNED Sprint 9    | PCI card token storage        |
| Midaz Ledger             | 8095 | Go (Docker)        | ACTIVE Sprint 8     | Core financial ledger         |
| Compliance API           | 8093 | Python (Docker)    | ACTIVE              | Sanctions + AML screening     |
| banxe-payment-core       | 8099 | Python/FastAPI     | PLANNED Sprint 9    | Adapter layer + webhook server|

Server: GMKtec (SSH key added to GitHub: banxe-payment-core)
VPN: Tailscale for remote access

---

## 7. Key Risks and Requirements

1. PCI DSS: Secure API encryption mandatory for card data
   Resolution: Hyperswitch vault + Paymentology Secure API (RSA+AES)

2. Remote API latency: Paymentology waits real-time for Deduct response
   Resolution: FastAPI async, Midaz query < 50ms, total < 200ms

3. High availability: Remote API webhook server must be 24/7
   Resolution: Docker restart policy, health checks, monitoring

4. Sanctions first (I-01): ALL transactions screened before authorization
   Resolution: compliance_bridge.screening called in auth_engine BEFORE Midaz

5. Mastercard license: GCMS access required for IPM settlement
   Resolution: TOMPAY as Principal Member has direct scheme access

6. Dual-run risk: 2-4 weeks parallel operation complexity
   Resolution: shadow mode only, no double-charging, reconciliation comparison

---

## 8. Sprint Plan

| Sprint   | Deliverable                                                    |
|----------|----------------------------------------------------------------|
| Sprint 9 | ADR-015 approved, banxe-payment-core scaffold, Hyperswitch deploy |
| Sprint 10| Paymentology integration, PaymentSwitchPort + IssuerPort adapters |
| Sprint 11| Settlement parser, Midaz<->Hyperswitch<->Paymentology E2E flow |
| Sprint 12| UAT, dual-run with TRIBE, compliance integration complete      |
| Sprint 13| Production cutover, TRIBE decommission, monitoring             |

---

## 9. Audit Results: What Already Exists

WHAT IS ALREADY DONE:
- Midaz Ledger deployed on GMKtec (port 8095, Sprint 8)
- ADR-013: Midaz as PRIMARY GL - already accepted
- ADR-014: Composable Financial Stack - already describes architecture with LedgerPort
- All invariants I-01 to I-31 documented and acknowledged
- banxe-emi-stack: compliance, AML, sanctions, FCA reporting active
- banxe-lexisnexis-distro: KYC integration active
- banxe-architecture: governance, INVARIANTS.md, SERVICE-MAP.md, STACK-LAYERS.md
- SSH key banxe-payment-core added to GitHub (CarmiBanxe)
- Repository banxe-payment-core created with initial scaffold

WHAT IS MISSING (built by this plan):
- Payment Switch (Hyperswitch) - Sprint 9
- Card Issuing integration (Paymentology) - Sprint 10
- Settlement/Reconciliation (Mastercard IPM) - Sprint 11
- Adapter layer: PaymentSwitchPort, IssuerPort - Sprint 9-10
- Full E2E authorization flow - Sprint 11
- UI: card management, transaction history - Sprint 12

---

## 10. Claude Code Prompt: STEP 1 + STEP 2

Copy and paste in Claude Code terminal on Legion:

```
Execute STEP 1 and STEP 2 of Variant B - Payment Processing Stack for TOMPAY UK EMI.

STEP 1: ADR-015 in banxe-architecture
1. cd ~/banxe-architecture && git pull origin main
2. Create decisions/ADR-015-payment-processing-stack.md
   (see MIGRATION-TRIBE-TO-COMPOSABLE.md in banxe-payment-core/docs/ for full ADR content)
3. Update SERVICE-MAP.md: add Hyperswitch ports 8096, 8097, 8098
4. git add . && git commit -m "ADR-015: Payment Processing Stack (Hyperswitch + Paymentology)" && git push origin main

STEP 2: Scaffold banxe-payment-core
1. cd ~/banxe-payment-core (already exists, git pull)
2. Create full src/ structure per docs/MIGRATION-TRIBE-TO-COMPOSABLE.md
3. Implement port interfaces: PaymentSwitchPort, IssuerPort, LedgerPort (ABCs)
4. Stub adapter implementations with TODO markers
5. Create docker/docker-compose.yml for Hyperswitch full stack (app-server :8096, control-center :8097, card-vault :8098, postgres, redis)
6. Run make quality-gate
7. git add . && git commit -m "feat: scaffold payment core adapters and Hyperswitch docker stack" && git push origin main

Follow INVARIANTS.md: I-01 (sanctions first), I-10 (no fake integrations), I-15 (no AGPLv3), I-20 (replaceable layers), I-24 (append-only audit), I-28 (instruction ledger discipline).
```
