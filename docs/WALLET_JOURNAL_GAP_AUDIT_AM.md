# Wallet journal gap audit (2026-11-28)

## Purpose and safety
This audit records wallet-changing paths found in `backend/main.py` that are not yet posted to the append-only double-entry journal. It is a code review of the current PR branch, not a production database audit. No historical rows were changed and no migration was run.

Do not merge or deploy on the strength of this document. Before historical migration, take a verified production database backup, reconcile the legacy `Tx` / `FinLedger` / `FinWallet` records against current `User` balances, and approve an opening-balance policy.

## Current journal coverage observed
- Approved coin purchases and purchased/promo coin issuance.
- Room gifts (including purchased-vs-promo spend, host earnings, commissions, platform share, Lucky gift payout).
- PK gifts and PK prize settlement.
- Withdrawal reserve and rejection reversal.
- Journal integrity and withdrawal reconciliation endpoints.

## Remaining wallet-changing paths identified

| Flow | Code path | Current balance behavior | Journal status / concern |
|---|---|---|---|
| Welcome bonus | login/register around `User(... coins=bonus)` | Adds welcome coins; `real_coins` remains zero, so bonus is promo | No double-entry journal posting |
| Daily reward | `POST /api/daily` | Adds coins; `real_coins` unchanged (promo) | Legacy budget + `FinLedger` only; no journal entry |
| Mission rewards | `POST /api/missions/claim` | Adds coins; `real_coins` unchanged (promo) | `Tx` only; no journal entry |
| Single-player games | `POST /api/games/<name>` | Bet is spent; payout is added as promo coins | No journal entry; game revenue, prize expense and promo-liability treatment need an approved policy |
| P2P create/cancel/join | `POST /api/games/p2p/*` | Stakes are spent; cancellation and winner prize are credited as promo coins | No journal entry; escrow, rake/platform share, refund and winner payout treatment need a defined policy |
| Wallet / seller operations | finance module and related admin routes | Legacy wallet/ledger tables are maintained separately | Must be traced end-to-end before migration; do not infer journal coverage from `FinLedger` rows alone |

## Important implementation details
- `spend_split()` identifies the purchased-coin portion of a spend; it is used by room gifts and PK gifts.
- `spend()` caps `real_coins` at the new total balance, effectively consuming promo coins before purchased coins.
- `add_coins()` increases `User.coins` only. Credits made through it are therefore promo balance unless the caller separately increases `real_coins` (purchase approval does so explicitly).
- The daily reward updates `User` and commits before recording the legacy budget spend and `FinLedger`; this is not yet one atomic double-entry transaction. It should be hardened before being treated as a finance-grade record.
- `FinLedger`, `FinWallet`, and `FinGiftTx` are legacy operational finance records. They are not substitutes for a balanced journal entry.

## Required next steps before implementation
1. Trace every seller/admin wallet route and identify all balance mutations, including commission and referral qualification paths.
2. Approve account mapping for promotional issuance, daily/mission rewards, game stakes/payouts, P2P escrow/refunds, seller balances, commissions and platform revenue.
3. Decide whether games and P2P remain enabled and obtain appropriate legal/payment-policy review for the relevant jurisdictions. Do not silently classify betting proceeds as ordinary revenue.
4. Implement each flow with the journal post in the same database transaction as the balance mutation; use stable idempotency keys and concurrency-safe claims.
5. Add tests for rollback, duplicate requests, concurrent claims, purchased-vs-promo allocation, and reconciliation.
6. Only after a production snapshot and signed-off opening-balance mapping, design a dry-run historical migration report. Do not write historical journal entries until the owner reviews and approves that report.

## UI verification boundary
The purchase-review modal has been code-inspected and its inline JavaScript syntax is checked in CI. No real browser interaction test has been performed yet; receipt loading, checkbox gating, approval/rejection, and review-history rendering still need manual or browser-automation verification.
