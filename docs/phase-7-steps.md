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
| 10 | PWA: `manifest.json`, icons, `sw.js` | To do |
| 11 | Deploy to Render + Neon Postgres (`psycopg[binary]`), env vars, point the gateway at production | To do |

## Decisions
- **Location:** the form no longer asks for latitude/longitude; meters are sited in Lagos (6.52, 3.38) by default. The `registry` message still records it. Phase 5's clear-sky check uses it.
- **One meter per account.** Disconnect posts `{"t":"meter.disconnect",...}` to the registry topic, then removes the meter. Re-registering the same meter ID continues from its highest genuine seq, so old signed readings can't be replayed for a second reward.
- **Power chart plots accepted readings only**: rejected readings aren't trustworthy measurements (e.g. a forged 147 W reading would distort the scale).
- **Impact factors:** 0.3 L diesel per kWh (typical small generator) and 2.68 kg CO₂ per litre of diesel; configurable via `DIESEL_L_PER_KWH` / `CO2_KG_PER_L`; labelled as estimates. Confirm sources before submission.
- Datetimes in queries are timezone-aware UTC (`as_utc`); SQLite returns naive UTC.
