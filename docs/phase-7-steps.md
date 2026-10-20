# Phase 7 — UI, PWA, impact page, deployment — progress

Goal (Plan.md): a polished, installable app on a public URL that judges can open. Design direction: `docs/ui-plan.md`, dark exchange-style look inspired by Bybit and Bitget (requested Oct 19).

| Step | What | Status |
|---|---|---|
| 1 | Design system: dark palette (ink / solar amber / green-red / teal proof), Inter + JetBrains Mono, Tailwind components, effects (glow, hover lift, live dot, row flash, skeletons, toasts); `_ui.html` macros (icons, logo, badges, verification steps, stat tiles, proof links, copy buttons, empty states) | Done 2026-10-19 |
| 2 | Layout: top nav, **account menu** (email, App ID, HashScan, Log out hidden until opened), phone bottom tab bar | Done 2026-10-19 |
| 3 | Public landing page `/` (hero, live numbers, how it works, verification pipeline, CTA) | Done 2026-10-19 |
| 4 | Dashboard (`/dashboard`, `/wallet` redirects): balances, meter status, today's stats, **live power chart** (Chart.js, accepted readings), latest decisions | Done 2026-10-19 |
| 5 | Readings: filter chips, per-reading verification steps, new rows flash | Done 2026-10-19 |
| 6 | Meter page: **no lat/lon**, one meter per account, device card, **Disconnect** (recorded on the registry topic) | Done 2026-10-19 |
| 7 | Market: exchange layout (ticker, best-price-first offers with depth bars, sell panel with % buttons, buy confirmation dialog, tabs); subtitle "Trade VKWH for HBAR." | Done 2026-10-19 |
| 8 | Public `/impact` page: verified energy, diesel / CO₂ estimates with the formula shown, rejection breakdown, latest proofs | Done 2026-10-19 |
| 9 | Tests: `tests/test_pages.py` (13) + updated meter/auth/readings tests → 163 passing | Done 2026-10-19 |
| 9a | **Reading detail** `/readings/{id}`: checks explained, canonical string, hash check, stored signature, meter key, **"Verify in your browser"** (TweetNaCl + Web Crypto, no server trust), live HCS message from the mirror node (`hedera.get_hcs_message`) with on-chain hash check | Done 2026-10-19 |
| 9b | **Getting-started checklist** on the dashboard (5 steps from real data; hides when done) | Done 2026-10-19 |
| 9c | **Live pop-ups** on every page: "+X VKWH earned" (coin animation) and "Reading #N rejected · CODE", via `/api/me/events`; dashboard balance refreshes itself | Done 2026-10-19 |
| 9d | **Price history chart** on the market (price per kWh line + volume bars) | Done 2026-10-19 |
| 9e | **Price band**: offers must be within `PRICE_MIN_HBAR_PER_KWH`–`PRICE_MAX_HBAR_PER_KWH` (defaults 1–50, testnet placeholders); live red/green preview in the sell panel | Done 2026-10-19 |
| 9f | Event branding removed from the product (footer); `tests/test_features.py` (19) → 182 passing | Done 2026-10-19 |
| 10 | PWA: `app/static/manifest.json`, icons (192/512, maskable), `/sw.js` (network-first, offline page), registered in `base.html` | Done 2026-10-20 |
| 10b | AI step removed from the UI (four checks), Phase 5 dropped | Done 2026-10-20 |
| 11a | Deploy prep: `psycopg[binary]`, `database_url()` (postgres → postgresql+psycopg), `pool_pre_ping`, HTTPS-only cookies on Render, `/healthz`, `render.yaml` blueprint; `tests/test_deploy.py` (7) → 216 passing | Done 2026-10-20 |
| 11b | Create Neon database + Render service, set secrets, first deploy, smoke test on the live URL | To do (you + me) |

## Decisions
- **Location:** the form no longer asks for latitude/longitude; meters are sited in Lagos (6.52, 3.38) by default. The `registry` message still records it. Phase 5's clear-sky check uses it.
- **One meter per account.** Disconnect posts `{"t":"meter.disconnect",...}` to the registry topic, then removes the meter. Re-registering the same meter ID continues from its highest genuine seq, so old signed readings can't be replayed for a second reward.
- **Power chart plots accepted readings only**: rejected readings aren't trustworthy measurements (e.g. a forged 147 W reading would distort the scale).
- **Impact factors:** 0.3 L diesel per kWh (typical small generator) and 2.68 kg CO₂ per litre of diesel; configurable via `DIESEL_L_PER_KWH` / `CO2_KG_PER_L`; labelled as estimates. Confirm sources before submission.
- Datetimes in queries are timezone-aware UTC (`as_utc`); SQLite returns naive UTC.
- **`readings.sig` column** (added Oct 19) via `add_missing_columns()` at startup: additive, nullable, keeps existing data. Readings before that show "signature not stored".
- **Price band** applies when listing. Offers listed before Oct 19 outside the band stay until cancelled.
- **Pop-ups** only report "settled" decisions (HCS done, or older than 30 s), so a reward toast never shows before the mint. The cursor is kept per browser tab (sessionStorage), so old readings never pop up.
