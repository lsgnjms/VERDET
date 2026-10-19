# VERDET UI/UX plan

Goal: a judge who opens the live URL understands VERDET in about 10 seconds and sees proof on Hedera in 2 clicks. Same stack (Jinja2 + HTMX + Tailwind CDN + Chart.js); no frontend framework.

## Principles
1. **Show the proof.** Every number links to its evidence on HashScan. "Verified on Hedera" is the recurring visual motif.
2. **Make verification visible.** Each reading shows its checks as a row of steps (registered → signature → sequence → physics → AI), green ticks or the red step that failed. This is VERDET's core idea, so the UI should show it, not hide it in a reasons column.
3. **Live, not static.** Things update by themselves (HTMX polling), with a subtle highlight when a new reading lands. Good for the demo video.
4. **Honest words** (rule 10): "verified-energy reward token", "trust-minimized and auditable". No "carbon credits", "trustless" or "real energy delivery".
5. **Phone-first**, because it's an installable PWA (Phase 7) and judges may open it on a phone.

## Visual identity (design tokens)
- **Colours:** solar amber `#F59E0B` (energy), emerald `#059669` (verified/accepted), slate neutrals, red `#DC2626` only for rejections. Light theme first; dark mode is optional polish.
- **Type:** Inter (Google Fonts) for UI, JetBrains Mono for IDs, hashes and numbers.
- **Logo:** a simple inline-SVG mark: a sun/leaf with a check, plus the wordmark "VERDET". Also used for the PWA icons.
- **Shape:** rounded-2xl cards, soft shadows, generous spacing; one accent per screen.
- **Reusable pieces** (Jinja macros in `templates/_ui.html`): `card`, `stat_tile`, `badge(decision)`, `hashscan_link`, `check_steps(reasons)`, `empty_state`, `button`.

## Pages (in priority order)

| # | Page | What changes | Phase |
|---|---|---|---|
| 1 | **Base layout** | Logo, top nav on desktop, **bottom tab bar on phones**, footer ("Hedera testnet · trust-minimized and auditable"), flash/toast area | 7 (do first) |
| 2 | **Dashboard** (`/wallet` becomes `/dashboard`) | Wallet card (App ID, HBAR, VKWH), 3 stat tiles (today's verified kWh, accepted vs rejected, meters online), **live power chart** (Chart.js, refresh every 5 s), last 5 readings with check steps | 7 |
| 3 | **Readings** | Check-steps row per reading; filter chips (all / accepted / rejected); new rows flash in; HashScan icons instead of text links | 3 → polish |
| 4 | **Marketplace** | Offer cards (amount, price, price per kWh, seller App ID), "List energy" sheet, buy confirmation, **success screen showing ONE transaction with both legs** + HashScan link, trade history | 6 |
| 5 | **Impact** (public `/impact`) | Big numbers (verified kWh, diesel litres displaced, CO₂ avoided) with the formula and source shown; active meters; latest HCS proofs feed | 7 |
| 6 | **Meters** | Device cards: online/offline dot (reading in the last 2 min), last seen, last seq, energy today; registration form in a collapsible panel | polish |
| 7 | **Auth pages** | Split layout: form + short "how VERDET works" panel | polish |
| 8 | **Landing page** (public `/`, approved Oct 19) | Hero ("Verified solar energy, rewarded and tradable on Hedera"), 4-step how-it-works, live impact numbers, buttons: Sign up / See the impact | 7 |

## Micro-interactions (cheap, high impact)
- Skeleton placeholders instead of "Loading…".
- `hx-swap` with a short fade; new readings highlighted for 2 s.
- Copy-to-clipboard with a "Copied" toast for App ID, tx IDs and the meter key.
- Numbers formatted by magnitude: mWh under 1 Wh, Wh, then kWh.
- Clear Hedera error messages ("Hedera is slow, try again") in a toast.

## Demo-video moments the UI should make obvious
1. Sign up → App ID appears instantly (Hedera account).
2. Real meter reading lands live → green check steps → VKWH balance ticks up.
3. Attack scripts → red rows appear live, each showing **which** check failed.
4. Marketplace buy → "1 transaction, 2 legs" success screen → HashScan.
5. Impact page, logged out.

## Out of scope (Plan.md §5 if wanted later)
Dark-mode toggle, animations library, charts beyond one live power chart, a React frontend.
