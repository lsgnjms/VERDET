# Phase 0 (Hedera half) + Phase 1 — progress

Plan: Steps 1–8 below. The Mega signing spike is split off and gates Phase 4 only (CLAUDE.md rule 12).

| Step | What | Status |
|---|---|---|
| 1 | `requirements.txt`, `.gitignore`, `.env.example` | Done 2026-10-12 |
| 2 | `app/hedera.py` (client, topics, token, mint, reward, accounts, atomic swap, mirror balances, HashScan links) | Done 2026-10-12 |
| 3 | `scripts/spike.py` — Phase 0 Hedera checkpoint | **Passed** 2026-10-12 |
| 4 | `scripts/setup_hedera.py` | Done 2026-10-12 |
| 5 | `app/models.py` + database (tables `users`, `meters`, `readings`, `offers`; partial unique index checked) | Done 2026-10-12 |
| 6 | `app/auth.py` (argon2, Fernet, session helpers; session parts tested in Step 8) | Done 2026-10-12 |
| 7 | `app/main.py` + templates (signup, login, logout, wallet card, HTMX balances) — smoke-tested with fake Hedera | Done 2026-10-12 |
| 8 | `tests/test_auth.py` (17 tests, fake Hedera, in-memory DB) + `tests/conftest.py`, `pytest.ini` | Done 2026-10-12 |

## Phase 1 checkpoint (Plan.md)
- [x] `pytest tests/test_auth.py` passes (17/17)
- [x] Database holds only encrypted keys (`gAAAA...`), checked on the real `verdet.db`
- [x] `.env` and `verdet.db` are git-ignored
- [x] First real signup: App ID `0.0.11006293`
- [x] Second user in a second browser shows a **different** App ID (`0.0.11006387`) that opens on HashScan

**Phase 1 checkpoint passed 2026-10-12.** Next: Phase 2 (meters and signed readings, using the simulator).

## Step 3 result (spike run, 2026-10-12)
Throwaway IDs from the spike (not the real VERDET topics/token):
- Topic `0.0.11005686`, spike VKWH token `0.0.11005688`
- Seller `0.0.11005689`, buyer `0.0.11005690`
- Atomic swap: https://hashscan.io/testnet/transaction/0.0.7179539-1791817398-478211402
  - Mirror node confirms **one** `CRYPTOTRANSFER`, `SUCCESS`, with both the VKWH legs (−/+500000) and the HBAR legs (+/−2 HBAR).
  - Final balances: seller 12 HBAR / 0.5 VKWH, buyer 8 HBAR / 0.5 VKWH.
- Rule 9 holds: no fallback needed.

## Step 4 result (real VERDET IDs, testnet, 2026-10-12)
| Name | ID |
|---|---|
| `registry` topic | `0.0.11006039` |
| `readings` topic | `0.0.11006041` |
| `trades` topic | `0.0.11006042` |
| VKWH token | `0.0.11006043` |

Mirror node confirms: token "VERDET kWh" / VKWH, 6 decimals, supply 0, treasury = operator, supply key set. All three topics have a submit key (only the operator can post).

## SDK facts confirmed while building `app/hedera.py` (hiero-sdk-python 0.3.0)
- `Client.from_env()` is **not used**: it parses `OPERATOR_KEY` with the generic loader, which reads a raw 32-byte key as Ed25519. `hedera.py` uses `from_string_ecdsa` / `from_string_ed25519` / `from_string_der`, chosen by `OPERATOR_KEY_TYPE` and `OPERATOR_KEY_FORMAT`.
- The SDK's key-parse errors include the key text; `hedera.py` swallows them and raises a redacted message.
- `execute()` does **not** raise on a failed receipt by default; `_run()` checks `receipt.status`.
- `add_token_transfer(token_id, account_id, amount)` confirmed.
- Automatic association: `AccountCreateTransaction.set_max_automatic_token_associations(1)` works — no separate associate transaction needed.
- User keys are stored as DER hex (`to_string_der`), which records the key type.
- `TransactionId.to_string()` doesn't zero-pad nanos; `hedera.py` formats IDs itself for HashScan.

## Notes
- Each spike run costs the operator ~20 HBAR + fees. Watch the operator balance.
- Mirror node lags consensus by a few seconds; the spike's balance check now waits for the post-swap amount.
