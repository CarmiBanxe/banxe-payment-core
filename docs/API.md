# API Reference — banxe-payment-core

## Ports (Abstract Interfaces)

### PaymentSwitchPort

| Method | Description |
|--------|-------------|
| `authorize(request)` | Authorize a payment (requires prior compliance screen) |
| `capture(txn_id, amount?)` | Capture an authorized payment |
| `refund(txn_id, amount, reason)` | Refund a captured payment |
| `void(txn_id)` | Void an authorized payment |
| `get_payment_status(txn_id)` | Get current payment status |

### IssuerPort (Companion API)

| Method | Description |
|--------|-------------|
| `issue_card(request)` | Issue a new Mastercard |
| `authorise_transaction(request)` | Process incoming card authorisation |
| `suspend_card(card_id, reason)` | Temporarily suspend a card |
| `cancel_card(card_id, reason)` | Permanently cancel a card |
| `get_card_status(card_id)` | Get current card status |

### LedgerPort (Midaz)

| Method | Description |
|--------|-------------|
| `get_balance(account_id, currency)` | Query available balance |
| `debit(entry)` | Debit an account |
| `credit(entry)` | Credit an account |
| `reserve(entry)` | Reserve funds (authorization hold) |
| `release_reservation(id)` | Release a hold |
| `settle_reservation(id, amount?)` | Convert hold to final debit |

## Amount Convention (I-05)

All amounts are **integers in minor units** (pence for GBP, cents for EUR/USD).
Never use float. Example: £10.50 = `1050`.

## Compliance Bridge

`ComplianceScreening.screen(request)` — must be called before any authorisation.
Returns `PASS`, `BLOCK`, or `REVIEW`.
`BLOCK` → decline. `REVIEW` → HITL. `PASS` → proceed to balance check.
