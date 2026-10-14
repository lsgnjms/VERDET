# Phase 2 — Meters and signed readings (simulator) — progress

Goal (Plan.md): a registered (simulated) meter streams signed readings, and the app rejects unregistered, tampered, replayed and duplicate ones.

| Step | What | Status |
|---|---|---|
| 1 | `gateway/canonical.py`: canonical string + SHA-256 hash | Done 2026-10-13 |
| 2 | `app/verify.py`: checks 1–3 (registered → signature → sequence) + `tests/test_verify.py` | Done 2026-10-13 |
| 3 | "Add meter" page (`/meters`) → `registry` HCS message, then database + `tests/test_meters.py` (8 tests) | Done 2026-10-13 |
| 4 | `POST /api/readings`: run checks, store every decision, advance `last_seq`, DB backstop → `DUPLICATE` + `tests/test_readings_api.py` (14 tests) | Done 2026-10-13 |
| 5 | `gateway/simulator.py` (PyNaCl key, Lagos solar curve, 1-min windows, `--fast` = every 5 s) + `tests/test_simulator.py` (5 tests); end-to-end run on a throwaway DB: 3/3 accepted | Done 2026-10-13 |
| 6 | `gateway/attacks/`: `unregistered.py`, `tamper.py`, `replay.py`, `duplicate.py` + `tests/test_attacks.py` (5 tests) | Done 2026-10-13 |

## Checkpoint (Plan.md)
- [x] `python gateway/simulator.py --fast` → readings stored as `accepted` (VD-SIM1 registered on testnet, confirmed by you)
- [x] Each attack script returns exactly its code against the running app: `UNREGISTERED`, `BAD_SIGNATURE`, `REPLAY`, `DUPLICATE` (all exit 0)
- [x] `pytest tests/test_verify.py` passes (33 tests); full suite 82 passed

**Phase 2 checkpoint passed 2026-10-13.** Next: Phase 3 (physics rules, HCS anchoring of every decision, VKWH rewards).

## Decisions
- **Wire format:** JSON with the canonical camelCase names plus `sig` (128 hex chars), e.g.
  `{"meterId":"VD-0001","seq":1,"tStartMs":0,"tEndMs":60000,"mWh":167,"peakMw":10120,"avgMv":18000,"avgMa":560,"sig":"…"}`.
  The Mega prints the same shape in Phase 4.
- **Keys:** raw 32-byte Ed25519 public key as 64 hex chars (same as `firmware/spike/spike.ino` prints); signature over the UTF-8 canonical string.
- **Malformed readings** (missing field, float, bad meterId) get HTTP 422 and are not decisions: there's nothing valid to sign or record.
- **`last_seq`** moves to `max(seq, last_seq)` for every reading with a valid signature (rule 7); never on `UNREGISTERED` / `BAD_SIGNATURE`.
- **HCS anchoring of reading decisions is Phase 3** (as Plan.md schedules it). In Phase 2 decisions are stored in the database only.
- **Meter registration:** the `registry` HCS message is sent first; the meter is saved only if Hedera succeeds. One public key can be registered to only one meter ID (otherwise one device could claim twice). The registry transaction link is shown once on success, not stored (storing it would need a new column, i.e. deleting `verdet.db`).
- **`POST /api/readings` responses:** 200 with `{"readingId","meterId","seq","decision","reasons","hash"}` for every decision (accepted or rejected); 422 with `{"error"}` for malformed input (not stored). No login: the signature is the credential.
- **Known limit (for the threat model):** anyone can POST readings naming unknown meters, and each is stored as `UNREGISTERED`. Rate limiting is out of scope for the prototype.
- **Simulator:** key seed + last seq in `gateway/.sim/<meterId>.json` (git-ignored, like the Mega's EEPROM). `tStartMs = seq × 60000` stands in for the Mega's ms-since-boot. Solar curve is a simple sine from 06:45 to 19:00 Lagos time (fixed UTC+1); pvlib replaces it in Phase 5. `--hour 12` forces midday for testing. `--spike` comes in Phase 3, `--lamp` in Phase 5. (`--night` was dropped on Oct 14 together with the night rule.)
- In Phase 2, "accepted" means it passed checks 1–3. Physics (Phase 3) and AI (Phase 5) slot into `verify_reading` later.
