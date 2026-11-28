# TWENTY ONE PARTY — Ethiopia-first Android Product Plan

**Decision recorded:** 2026-11-28  
**Initial market:** Ethiopia  
**Primary client experience:** Installable Android PWA (Chrome Add to Home Screen; standalone display, app icon, no Chrome toolbar)  
**Long-term direction:** iOS and international markets

This document records the launch decisions and implementation sequence. It is a product/engineering plan, not a claim that the Android client or production infrastructure already exists.

## 1. Product position

TWENTY ONE PARTY is a social entertainment platform combining live video, voice rooms, PK battles, short-video posts, creator profiles, virtual gifts, and a controlled creator economy.

The first release should be optimized for users in Ethiopia, with architecture and data models that do not hard-code Ethiopia as the only future country.

## 2. Ethiopia launch requirements

- Languages: Amharic and English at launch. Keep all user-facing strings localizable; add Afaan Oromo and other languages in later releases based on user research.
- Local time: use Africa/Addis_Ababa for user-facing dates, daily rewards, and reports. Store server timestamps in UTC.
- Currency display: ETB for local purchase and payout screens. Store money in integer minor units (santim) and coins as integers; never use binary floating point for financial calculations.
- Phone numbers: normalize Ethiopian numbers consistently, including +251 and local 09/07 formats. Do not expose phone numbers in public profile IDs.
- Payments: Telebirr is the planned local payment rail. Do not enable automated collection or payouts until the merchant/API arrangement and reconciliation process are verified. Manual review must verify the actual transaction in the merchant account; a screenshot alone is not proof.
- Connectivity: design for intermittent mobile data, reconnects, low bandwidth, and graceful media-quality reduction. Keep chat/control events usable when media is degraded.
- Device support: test budget Android devices, limited memory, older supported Android versions, background/foreground transitions, permissions, and battery use.
- Safety: age policy, reporting, blocking, moderation escalation, and creator/agency verification must be defined before public launch.

## 3. Android client direction — keep the installed PWA

The owner explicitly uses and wants to keep the current Chrome-installed Progressive Web App (PWA): it is added to the Android home screen, opens in standalone app mode, uses the TWENTY ONE PARTY launcher icon, and does not show Chrome browser controls. **Do not replace this with a native Kotlin/Compose app.**

The existing client is `templates/index.html`, with `templates/manifest.json` and `templates/sw.js`; Flask serves `/manifest.json` and `/sw.js`. The HTML registers the service worker at the root scope. This is the primary Android product.

Implementation priorities:
1. Preserve the PWA install identity: stable app name, correct 192×192 and 512×512 launcher icons, `display: standalone`, root scope, and HTTPS deployment.
2. Test the real Android Chrome installation flow: install prompt / Add to Home Screen, launch from home screen, splash/icon, standalone display, updates, and uninstall/reinstall.
3. Improve service-worker update behavior and offline fallback without caching authenticated API responses or private user data.
4. Optimize the current responsive UI for Android touch, safe areas, smaller screens, memory, battery, and unstable mobile networks.
5. Keep backend business rules server-side. The PWA must not calculate trusted coin purchases, gift splits, commissions, withdrawal eligibility, or payout status.
6. Consider native Android only if a later, clearly identified requirement cannot be delivered reliably through the PWA, and only after the owner approves that change.

## 4. First Android release scope

### P0 — Required for a controlled beta
- Sign up/login, logout, session expiry, profile and avatar
- Home/discovery with live and voice rooms
- Create/join/leave voice rooms, seat requests, mute, host controls
- Live room join/watch, chat, gifts, audience list, follow
- Basic PK battle lifecycle and results
- User search, follow/unfollow, block/report
- Notifications inbox and reconnect handling
- Admin moderation tools and audit trail
- Amharic/English localization, ETB display, Addis Ababa time
- Crash reporting, privacy notice, terms, account deletion request

### P1 — After beta feedback
- Short-video upload/feed, likes, comments, shares
- Creator dashboard, host applications, agency/agent tools
- Advanced discovery and ranking
- Creator activity rewards, after abuse controls and budget caps are tested
- More languages and country configuration

### Not in the first money-enabled beta
- Lucky/lottery mechanics and cash-prize games
- Unverified USDT withdrawals or other unsupported payout rails
- Any automatic payout that cannot be reconciled against an official provider record

## 5. Backend/API readiness work

Before the Android client is implemented, create and maintain an API contract covering:
- Authentication and token refresh/revocation
- User/profile and account deletion
- Room state, seat/guest membership, presence and moderation
- Live/RTC token issuance and short-lived permissions
- Chat, gifts, PK state transitions and idempotency keys
- Feed upload, media metadata, moderation and CDN URLs
- Notifications and device-token registration
- Admin roles, audit events and finance reporting

All state-changing endpoints need authorization checks, input validation, rate limits, and safe retry behavior. Use server-issued IDs and idempotency keys for purchases, gifts, PK settlement, and payout requests.

## 6. Delivery sequence

1. **Baseline audit:** map existing screens, routes, data models, external services, and test coverage. Keep the current main branch untouched.
2. **API contract:** document existing endpoints and identify missing/unsafe contracts before creating the Android client.
3. **Experience completion:** finish the agreed core social/live/voice workflows in the current client where possible, with regression tests.
4. **Android foundation:** create the Kotlin/Compose app, secure session storage, API client, navigation, localization, and crash reporting.
5. **Media integration:** integrate Android RTC with server-issued short-lived tokens; test real devices and network transitions.
6. **Closed beta in Ethiopia:** invite a small group, monitor crashes, latency, retention, moderation load, and per-user media costs.
7. **Finance/security gate:** reconcile the ledger, verify payment/payout operations, threat-model critical flows, run load and recovery tests.
8. **Public launch:** expand gradually only when operational metrics and support capacity are acceptable.

## 7. Launch metrics and financial guardrails

Track at minimum:
- Signup completion, daily/weekly active users, D1/D7 retention
- Room joins, average listening/viewing time, host activation and repeat hosting
- Crash-free sessions, RTC connection success, reconnect rate, median/p95 join latency
- Reports per 1,000 active users and moderation response time
- Coin purchase conversion, gift volume, host liabilities, pending withdrawals
- Revenue less payment fees, media/bandwidth, hosting, moderation, refunds, fraud, tax, and support costs

The owner's 30% net-margin objective is a target, not a guarantee. Do not declare a profitable launch from the gross gift split alone. Use actual cost and liability data, and pause expansion if contribution margin or payout reserves become unsafe.

## 8. Release gates

Do not merge to `main`, enable real-money payouts, or announce production readiness until:
- Automated tests and migration tests pass on a clean database
- Financial balances reconcile and all transitions are idempotent
- Secrets and production configuration fail safely
- Android device and network tests pass
- Backup/restore and incident-response procedures are verified
- Privacy, safety, age, and local legal/payment requirements have been reviewed

## 9. Current repository facts

- Existing client: responsive web/PWA in `templates/index.html`
- Backend: Flask under `backend/`
- Existing RTC integration: Agora web SDK in the PWA and server-side token builder
- Native Android project: not present at the time this plan was written
- Existing security/withdrawal work: draft PR `#1`, not merged; it still requires review and runtime tests
