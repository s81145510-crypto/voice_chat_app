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


## Seller / reseller route trace (source review completed)
Reviewed `backend/seller.py` at the current branch head. The following live paths are separate from the core purchase journal:

| Route | Mutation | Accounting requirement |
|---|---|---|
| `POST /api/seller/order` | Creates a pending wholesale order with Birr, payment reference and screenshot | No wallet mutation until approval; needs payment-review evidence and duplicate-safe decision |
| `POST /api/seller/admin/order` approve | Credits `coins` with trade coins + bonus; increases `real_coins` only by trade coins; updates seller lifetime purchase totals | Must post a COIN purchased-liability issuance and separate promo-liability issuance in the same transaction. Birr cash/clearing side must not be posted as settled cash until manual payment verification is evidenced |
| `POST /api/seller/transfer` | Debits seller trade balance by gross amount, credits buyer by net amount, removes fee; records `SellerTransfer` and two `Tx` rows | **Now journal-integrated:** debit seller purchased-coin liability by gross; credit buyer purchased-coin liability by net; credit COIN-denominated transfer-fee revenue by fee. Journal and wallet changes commit in the same transaction |
| `POST /api/seller/admin/seller` | Approve/reject/suspend/tier changes | No wallet movement; should have admin audit trail but is not itself a journal transaction |

### Seller-specific safety findings
- Seller transfer currently uses a conditional SQL update requiring both `User.coins >= amount` and `User.real_coins >= amount`; promo coins cannot be transferred. It credits the recipient's `coins` and `real_coins` by net and stores the 2% fee.
- Seller transfer now posts a balanced COIN journal entry atomically with wallet changes, the `SellerTransfer` row and `Tx` rows.
- **Client retry safety added:** every transfer requires a client request ID, stored in a unique `SellerTransfer.request_id`. Exact retries return the original transfer result; reusing the same key with a different seller/buyer/amount returns HTTP 409. A unique-index race also rolls back the losing transaction and returns the already committed result when the payload matches.
- The seller page persists the pending request ID in local storage until the server confirms success, so a retry after a lost response reuses the same key. If a pending request exists with different form values, the UI blocks the new transfer until the original buyer/amount is retried.
- Added an additive startup migration for existing `seller_transfer` tables because `db.create_all()` does not add new columns to existing tables.
- The seller wholesale approval route conditionally changes order status from pending, but its approval credits trade and promo coins without journal entries or a payment-review audit record equivalent to the main customer purchase review.
- Seller wholesale order methods currently accept `telebirr` or `usdt`, but this route is not shown using the main Telebirr manual-verification / TRON verifier policy. This is a release-blocking payment-control gap; do not approve seller USDT orders as if chain-verified.
- Seller transfer fee is a platform fee in COIN units, not ETB cash received. It must not be mixed into Birr revenue without a real settlement/conversion event.

### Seller implementation order
1. Add seller-order review parity: evidence record, manual Telebirr verification, and reuse the read-only TRON verification path for USDT; preserve conditional pending-to-final status transition.
2. **Completed:** stable client transfer request IDs, unique DB index, exact retry replay, mismatched payload rejection, and persisted client retry key. Integration tests cover exact retry without double debit, mismatched payload rejection, and missing key rejection.
3. **Completed:** transfer journal lines in the same transaction as both wallet updates and `SellerTransfer` insertion. Regression tests validate gross/net/fee allocation and reject invalid splits.
4. Add wholesale issuance journal entries only after an explicit approved payment-control design. Keep Birr cash-clearing and COIN liability ledgers separate; never imply that a screenshot itself proves settlement.
5. Add tests for seller-to-buyer transfer split (gross/net/fee), promo-balance exclusion, concurrent duplicate transfer, exact retry, journal failure rollback, and seller wholesale duplicate approval / payment-verification gating.

## UI verification boundary
The purchase-review modal has been code-inspected and its inline JavaScript syntax is checked in CI. No real browser interaction test has been performed yet; receipt loading, checkbox gating, approval/rejection, and review-history rendering still need manual or browser-automation verification.
