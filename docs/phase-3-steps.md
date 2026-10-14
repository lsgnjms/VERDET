# Phase 3 — Physics rules, on-chain proof, rewards — progress

Goal (Plan.md): every decision is anchored on Hedera, and accepted energy mints VKWH to the meter's owner.

| Step | What | Status |
|---|---|---|
| 1 | Physics checks in `app/verify.py` (`PHYSICS_LIMIT`) + 10 tests | Done 2026-10-14 |
| 2 | `/api/readings`: accepted → mint + transfer VKWH to owner; every decision → `readings` HCS topic; store tx IDs + `tests/test_anchoring.py` (8 tests). Real testnet smoke test: VD-SIM1 seq 8 → 142 mWh minted, sent to `0.0.11006293`, decision is message #1 on topic `0.0.11006041` | Done 2026-10-14 |
| 3 | `/readings` page + `/readings/table` HTMX partial (every 5 s): time (Lagos), meter, seq, energy, decision + reasons, HCS proof link, mint link; verified vs rewarded totals + `tests/test_readings_page.py` (9 tests) | Done 2026-10-14 |
| 4 | Simulator flag `--spike` (signed, ~3× rating, any hour) + 2 tests | Done 2026-10-14 |
| 5 | Checkpoint run on testnet | **Passed** 2026-10-14 |

## Checkpoint (Plan.md)
- [x] 10 simulated windows → 10 messages on the `readings` topic (`0.0.11006041`, topic seq 2–11), all `accepted` with `mintTx`
- [x] Owner's VKWH balance equals the sum of accepted mWh: `0.0.11006293` went 142 → 1,624 units (+1,482), and the 10 accepted readings (VD-SIM1 seq 9–18) total 1,482 mWh
- [x] `--spike` rejected with `PHYSICS_LIMIT` (seq 19, claimed 499 mWh / 30,873 mW peak), and that rejection is on HCS too (topic seq 12)
- All 11 new readings have `hcs_tx` stored; all 10 accepted have `mint_tx`. Verified on the public mirror node, not just in the app. Full suite: 111 tests passing.

**Phase 3 checkpoint passed 2026-10-14.** Next: Phase 4 (the real meter; needs the Mega signing spike first).

## Decisions
- **Night rule dropped (Oct 14).** The demo is filmed in daylight, and evening hardware testing (Phase 4) must work. `NIGHT_GENERATION` and `--night` are removed from CLAUDE.md and Plan.md; the idea is in Plan.md §5. Phase 5's clear-sky check will still treat night-time energy as anomalous.
- **Physics rules** (integer maths, no rounding):
  - `peakMw × 10 ≤ rated_w × 1000 × 11` (peak at most 1.1 × rating)
  - `mWh × 3,600,000 ≤ peakMw × window_ms` (energy at most peak × window); a zero or negative window fails
  - Either failure → `PHYSICS_LIMIT`. The signature was valid, so `last_seq` still advances (rule 7).
- **Pipeline order:** decide → save to DB → (accepted and mWh > 0) mint into treasury → transfer to owner → post the decision to the `readings` topic → save tx IDs. A Hedera failure never undoes a decision: the missing ID means "reward pending" or "not anchored", and the gateway's answer includes `hederaError`. No automatic retry (prototype limit).
- **Where tx IDs live:** `Reading.mint_tx` (mint) and `Reading.hcs_tx` (HCS). The reward transfer's ID goes in the HCS message (`rewardTx`) and the API answer only; storing it would need a new column, i.e. deleting `verdet.db`.
- **HCS `reading` message:** Plan.md schema, plus `mWh`/`peakMw` on rejections too (shows what a spike claimed) and `rewardTx` on accepted readings. `aiScore` arrives in Phase 5.
- **Zero-energy accepted readings** (dark windows) are anchored but not minted (minting 0 is invalid).
- **Threat-model note (option 1, chosen Oct 14):** `/api/readings` is public by design (the signature is the credential). Anyone can post junk readings naming unknown meters (`UNREGISTERED`) or real meter IDs with garbage signatures (`BAD_SIGNATURE`). They can't mint or move `last_seq`, but each costs the operator one HCS fee (~$0.0001) plus a DB row. Accepted for testnet; mitigations for later: gateway token, rate limiting.
- **Readings page:** shows only readings from the logged-in user's meters (newest 50). `UNREGISTERED` junk belongs to no owner, so nobody sees it. The summary shows verified (accepted) mWh, and a "rewarded on Hedera" line when the two differ.
- **Old Phase 2 readings** in `verdet.db` (seq 0–7 of VD-SIM1) were accepted before rewards existed, so they show "not anchored" / "pending" (1,198 mWh never minted). **Kept as history (decided Oct 14).** They don't affect the main demo, which runs on the deployed app's fresh database (Phase 7). The Step 5 checkpoint compares the owner's balance against the readings that run produces.
- Order stays fixed: registered → signature → sequence → physics → (AI in Phase 5). A replayed spike reports `REPLAY`, not `PHYSICS_LIMIT`.
