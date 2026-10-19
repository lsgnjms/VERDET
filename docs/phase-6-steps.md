# Phase 6 — Marketplace with atomic swaps — progress

Goal (Plan.md): users sell verified energy to each other for HBAR in one atomic transaction.

| Step | What | Status |
|---|---|---|
| 1 | `/market` page: sell form (with available balance), open offers from others, your offers (cancel), recent trades with HashScan links | Done 2026-10-19 |
| 2 | `POST /offers`: parse amounts exactly (Decimal → mWh / tinybars), refuse more than balance minus already-listed | Done 2026-10-19 |
| 3 | `POST /offers/{id}/buy`: DB lock (open → locked in one UPDATE), HBAR check, **one `atomic_swap` TransferTransaction signed by seller + buyer keys**, filled + tx ID, `trades` HCS message | Done 2026-10-19 |
| 4 | `POST /offers/{id}/cancel`: own open offers only | Done 2026-10-19 |
| 5 | `tests/test_market.py` (25 tests, fake Hedera) | Done 2026-10-19 |
| 6 | Checkpoint on testnet with two real users | **Passed** 2026-10-19 |

## Checkpoint (Plan.md), adjusted for real balances
Plan.md says "User A lists 0.5 VKWH for 2 HBAR". 0.5 VKWH = 500 Wh, about 50 hours of a 10 W panel, and the test seller (`0.0.11006293`) holds about 0.0016 VKWH. So the checkpoint uses **0.001 VKWH for 2 HBAR**. The mechanism is identical.
- [x] User A (`0.0.11006293`) listed 0.0013 VKWH for 5 HBAR; User B (`0.0.11083341`) bought it (offer 1)
- [x] **One** transaction: the mirror node returns a single `CRYPTOTRANSFER` `SUCCESS` (`0.0.7179539@1792402780.213973760`) with the HBAR legs (±5 HBAR) and the VKWH legs (±1,300 units)
- [x] Balances moved in that transaction; the offer shows `filled`
- [x] A second buy attempt fails with a clear message (`test_second_buy_fails_with_clear_message`)

**Phase 6 checkpoint passed 2026-10-19.**

## Decisions
- **Offer states:** `open` → `locked` (purchase in progress) → `filled`, or `cancelled`. The lock is one conditional SQL UPDATE (`WHERE status='open'`), so only one buyer can win, even with two clicks at once.
- **Listing guard:** amount ≤ on-chain VKWH balance (mirror node) − VKWH already in your open or locked offers. If the mirror node can't be reached, listing is refused instead of guessed.
- **Swap failure:** the offer goes back to `open` and nothing moved (the transaction is atomic). If Hedera says the seller lacks the VKWH (`INSUFFICIENT_TOKEN_BALANCE`), the offer is `cancelled` instead.
- **Fees:** the operator pays Hedera fees; buyer and seller only sign their own legs.
- **Amounts:** input "0.5" VKWH / "2" HBAR is parsed with `Decimal` into exact integers; max 6 / 8 decimals; capped at 2⁶³−1 units (Hedera's 64-bit limit).
- **`trades` HCS message** is best effort after the swap: the swap itself is the final, public record on Hedera.
