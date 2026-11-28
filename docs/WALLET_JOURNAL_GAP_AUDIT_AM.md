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
| Welcome bonus | login/register around `User(... coins=bonus)` | Adds welcome coins; `real_coins` remains zero, so bonus is promo | **Journal-integrated:** promo issuance debit / user promo-liability credit in the account-creation transaction; guests receive no bonus |
| Daily reward | `POST /api/daily` | Adds coins; `real_coins` unchanged (promo) | **Journal-integrated:** promo liability entry and wallet claim commit atomically; legacy reward-budget spend and `FinLedger` are still recorded afterward in a separate commit |
| Mission rewards | `POST /api/missions/claim` | Adds coins; `real_coins` unchanged (promo) | **Journal-integrated:** promo issuance entry commits atomically with claim, wallet credit and `Tx`; stable per-user/day/mission idempotency key |
| Single-player games | `POST /api/games/<name>` | Bet is spent; payout is added as promo coins | No journal entry; game revenue, prize expense and promo-liability treatment need an approved policy |
| P2P create/cancel/join | `POST /api/games/p2p/*` | Stakes are spent; cancellation and winner prize are credited as promo coins | No journal entry; escrow, rake/platform share, refund and winner payout treatment need a defined policy |
| Admin wallet grant | `POST /api/admin/user` with `action=grant` | Positive grants increase `coins` only (promo); negative grants subtract total `coins` through `add_coins()` without adjusting `real_coins` | **Not journaled.** Negative grants can make `real_coins > coins`; needs a guarded purchased/promo allocation and approved adjustment account before enabling negative adjustments |
| Wallet / seller operations | finance module and related admin routes | Legacy wallet/ledger tables are maintained separately | Must be traced end-to-end before migration; do not infer journal coverage from `FinLedger` rows alone |

## Important implementation details
- `spend_split()` identifies the purchased-coin portion of a spend; it is used by room gifts and PK gifts.
- `spend()` caps `real_coins` at the new total balance, effectively consuming promo coins before purchased coins.
- `add_coins()` increases `User.coins` only. Credits made through it are therefore promo balance unless the caller separately increases `real_coins` (purchase approval does so explicitly).
- Daily wallet credit, claim marker, `Tx`, and COIN journal posting now share one transaction. The legacy reward-budget spend and `FinLedger` are still recorded afterward in a separate commit; that operational budget trail is not yet fully atomic with the wallet/journal transaction.
- `FinLedger`, `FinWallet`, and `FinGiftTx` are legacy operational finance records. They are not substitutes for a balanced journal entry.
- The admin `grant` action is still a financial gap: positive amounts are promo credits but have no journal entry; negative amounts call `add_coins()` and can reduce the total below `real_coins`. Do not use negative grants as a production correction until the purchased/promo split and contra-account policy are implemented and tested.

## Required next steps before implementation
1. **Completed for reward issuance:** welcome, daily and mission promo coin credits now post balanced COIN journal entries in the same transaction as their wallet mutations; tests cover daily/mission posting and rollback.
2. Trace remaining seller/admin wallet routes and identify all balance mutations, including commission and referral qualification paths.
3. Approve account mapping for game stakes/payouts, P2P escrow/refunds, seller balances, commissions and platform revenue.
4. Decide whether games and P2P remain enabled and obtain appropriate legal/payment-policy review for the relevant jurisdictions. Do not silently classify betting proceeds as ordinary revenue.
5. Implement remaining flows with journal posts in the same database transaction as balance mutations; use stable idempotency keys and concurrency-safe claims.
6. Only after a production snapshot and signed-off opening-balance mapping, design a dry-run historical migration report. Do not write historical journal entries until the owner reviews and approves that report.


## Seller / reseller route trace (source review completed)
Reviewed `backend/seller.py` at the current branch head. The following live paths are separate from the core purchase journal:

| Route | Mutation | Accounting requirement |
|---|---|---|
| `POST /api/seller/order` | Creates a pending wholesale order with Birr, payment reference and screenshot | No wallet mutation until approval; needs payment-review evidence and duplicate-safe decision |
| `POST /api/seller/admin/order` approve | Reviews payment evidence, conditionally approves/rejects, credits trade + promo coins and updates seller lifetime purchase totals | **Now hardened:** receipt required; Telebirr approval requires explicit manual confirmation; USDT approval uses the read-only TRON verifier; reviewer/action/reference/reason are audited; ETB clearing/deferred proceeds and COIN trade/promo issuance journal entries commit atomically with wallet changes |
| `POST /api/seller/transfer` | Debits seller trade balance by gross amount, credits buyer by net amount, removes fee; records `SellerTransfer` and two `Tx` rows | **Now journal-integrated:** debit seller purchased-coin liability by gross; credit buyer purchased-coin liability by net; credit COIN-denominated transfer-fee revenue by fee. Journal and wallet changes commit in the same transaction |
| `POST /api/seller/admin/seller` | Approve/reject/suspend/tier changes | No wallet movement; should have admin audit trail but is not itself a journal transaction |

### Seller-specific safety findings
- Seller transfer currently uses a conditional SQL update requiring both `User.coins >= amount` and `User.real_coins >= amount`; promo coins cannot be transferred. It credits the recipient's `coins` and `real_coins` by net and stores the 2% fee.
- Seller transfer now posts a balanced COIN journal entry atomically with wallet changes, the `SellerTransfer` row and `Tx` rows.
- **Client retry safety added:** every transfer requires a client request ID, stored in a unique `SellerTransfer.request_id`. Exact retries return the original transfer result; reusing the same key with a different seller/buyer/amount returns HTTP 409. A unique-index race also rolls back the losing transaction and returns the already committed result when the payload matches.
- The seller page persists the pending request ID in local storage until the server confirms success, so a retry after a lost response reuses the same key. If a pending request exists with different form values, the UI blocks the new transfer until the original buyer/amount is retried.
- Added an additive startup migration for existing `seller_transfer` tables because `db.create_all()` does not add new columns to existing tables.
- Seller wholesale approval now conditionally changes status from pending, requires a receipt, enforces manual Telebirr verification or read-only TRON verification for USDT, writes a review audit record, and journals ETB/COIN entries atomically with wallet changes.
- Seller transfer fee is a platform fee in COIN units, not ETB cash received. It must not be mixed into Birr revenue without a real settlement/conversion event.

### Seller implementation order
1. **Completed:** seller-order review parity: receipt evidence, manual Telebirr confirmation, read-only TRON verification for USDT, review audit, atomic journal posting, and conditional pending-to-final status transition.
2. **Completed:** stable client transfer request IDs, unique DB index, exact retry replay, mismatched payload rejection, and persisted client retry key. Integration tests cover exact retry without double debit, mismatched payload rejection, and missing key rejection.
3. **Completed:** transfer journal lines in the same transaction as both wallet updates and `SellerTransfer` insertion. Regression tests validate gross/net/fee allocation and reject invalid splits.
4. **Completed:** seller wholesale issuance journal entries after payment-control checks. Keep Birr cash-clearing and COIN liability ledgers separate; a screenshot alone is not treated as proof of settlement.
5. Add tests for seller-to-buyer transfer split (gross/net/fee), promo-balance exclusion, concurrent duplicate transfer, exact retry, journal failure rollback, and seller wholesale duplicate approval / payment-verification gating.

## UI verification boundary
The main admin panel now includes Seller Orders and Seller Reviews tabs, receipt inspection, Telebirr checkbox gating, rejection reason capture, and seller review-history rendering. Purchase and seller review inline JavaScript syntax is checked in CI. No real browser interaction test has been performed yet; receipt loading, checkbox gating, approval/rejection, and review-history rendering still need manual or browser-automation verification.


### Seller wholesale review implementation notes
- `POST /api/seller/admin/order` accepts only `approve` or `reject`; rejection requires a reason.
- Every decision requires the uploaded receipt. Telebirr approval requires `manual_verified=true`; the admin must compare the reference against the actual incoming Telebirr payment.
- USDT order references are normalized as 64-character lowercase transaction hashes. Approval calls the existing read-only TRON verifier with the configured receiving address and expected amount; pending chain confirmation returns a retryable conflict, invalid evidence is rejected.
- A new `SellerOrderReview` record stores reviewer, timestamp, action, reference snapshot and note. The status claim, audit record, wallet credits, and ETB/COIN journal postings are atomic.
- No live external Telebirr or TRON payment was verified during CI. Manual production verification remains required.
- API regression tests are present, and the seller review UI is now implemented in the main admin panel. No browser-based seller-admin review interface test has been performed.
