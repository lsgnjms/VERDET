# Phase 6b — Price per kWh + redeem VKWH for airtime/data — progress

Added Oct 19 (Plan.md Phase 6b). Option A chosen for delivery: the VKWH spend and burn are real on Hedera; airtime/data is a labelled demo voucher.

| Step | What | Status |
|---|---|---|
| 1 | Sell form asks **price per kWh** (pre-filled suggested price = last trade inside the band, else `PRICE_REFERENCE_HBAR_PER_KWH`=10; Market/±5% buttons; slider within the band); total computed exactly in tinybars (`offer_total`); "= 400 mWh" helper; minimum offer 0.0001 VKWH | Done |
| 2 | `hedera.return_to_treasury` (user key signs) + `hedera.burn_vkwh` (`TokenBurnTransaction`, supply key) | Done |
| 3 | `Redemption` table (masked phone only), `/rewards` page (demo banner, catalogue, redeem dialog, history with voucher + return/burn/record links), `POST /rewards/redeem` | Done |
| 4 | `redemptions` HCS topic `0.0.11090339` created by the updated `setup_hedera.py` (now creates only what's missing) | Done — **paste `REDEMPTIONS_TOPIC_ID=0.0.11090339` into `.env`** |
| 5 | Nav (Rewards tab; Impact moved to the account menu on phones), dashboard Redeem button, impact "Rewards redeemed", landing copy | Done |
| 6 | Tests: `tests/test_rewards.py` (23) + updated market/feature tests → 209 passing | Done |
| 7 | Checkpoint on testnet: redeem one item; HashScan shows the return transfer, the burn, and the `redemptions` message | **Passed** 2026-10-20: ₦100 airtime by `0.0.11006293`; mirror node shows `CRYPTOTRANSFER` (−500 user / +500 treasury), `TOKENBURN` (−500), `CONSENSUSSUBMITMESSAGE`, all `SUCCESS`; VKWH total supply 3,148 → 2,648 |

## Decisions
- **Catalogue (demo rates):** ₦100 / ₦200 / ₦500 airtime = 0.0005 / 0.001 / 0.0025 VKWH; 100 MB / 500 MB / 1 GB data = 0.0004 / 0.0015 / 0.0028 VKWH. Real energy value is far lower (a 10 W panel makes ~0.05 kWh a day), so the UI says "demo rates".
- **Privacy:** only the masked phone (`+234******4567`) is stored; the HCS message has no phone at all (HCS is public forever).
- **Order:** guards (item, network, phone, topic configured, available = balance − listed) → return to treasury (if it fails, nothing moved) → burn (if it fails, status `burn_pending`, VKWH sits in the treasury) → DB record → HCS record (best effort).
- **Networks:** MTN, Airtel, Glo, 9mobile. Phone: Nigerian mobiles (`0[789]…`, `234…`, `+234…`).
