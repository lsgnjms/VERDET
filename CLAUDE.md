# CLAUDE.md — VERDET

Read this first in every session. The full plan is in `Plan.md`. This file holds the rules for working on the codebase.

## Project
**VERDET** (VERifiable Decentralized Energy Trading): a hackathon prototype for the IEEE ClimateChain Global Hackathon, Track: Renewable Energy & Energy Trading. Deadline: Oct 25, 2026, 3:00 pm Lagos.

Flow: Arduino Mega meter measures solar output and **signs** each reading window → PC gateway relays → FastAPI app verifies (identity, signature, replay, duplicate, physics, AI) → every decision is recorded on a Hedera HCS topic → accepted energy mints VKWH (HTS token) to the owner → users trade VKWH for HBAR on a fixed-price marketplace (one atomic transaction) → public impact page.

Precedent: GreenAfrica (2025 Hedera Africa Hackathon winner). Keep the build that simple.

## About the developer
- Comfortable with Python; **this is their first Python backend**. Strong in electrical/industrial engineering and Arduino.
- 4 hours a day. Work phase by phase, following `Plan.md`.
- **Explain as you go.** For each new file or concept, give a short plain-English note on what it does and why. No long lectures.
- Deliver code in **small, runnable pieces**, and end each piece with the exact command to check it.

## Stack (locked; don't change without asking)
- Python 3.11+, FastAPI, Uvicorn, Jinja2 templates, HTMX, Tailwind (CDN), Chart.js
- SQLModel: SQLite locally, Postgres (Neon) in production, chosen by `DATABASE_URL`
- Hedera: the official Hiero Python SDK; mirror node REST via `httpx`
- PyNaCl (Ed25519 verify), `cryptography` (encrypting custodial keys), argon2 (passwords)
- pvlib + scikit-learn (AI verifier), pyserial (gateway), pytest
- Arduino Mega firmware in C++ with the rweather `Crypto` library
- Deploy: Render

**Not used, by decision:** TypeScript/React/Next.js, Guardian, Solidity/smart contracts, wallet-connect, a separate AI service, a direct "send tokens" feature. Don't add any of these.

## Repo layout
```
app/        main.py, models.py, auth.py, hedera.py, verify.py, ai.py, templates/, static/
gateway/    canonical.py, simulator.py, serial_bridge.py, attacks/
firmware/   meter/meter.ino
scripts/    spike.py, setup_hedera.py
tests/      pytest tests
docs/       architecture, threat model, wiring
```

## Commands
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload            # run the app at http://127.0.0.1:8000
pytest                                   # run all tests
python scripts/setup_hedera.py           # one-time: create topics + VKWH token
python gateway/simulator.py --fast       # simulated meter (flags: --spike --night --lamp)
python gateway/serial_bridge.py --port COM3   # real meter
```

## Hard rules
1. **Testnet only.** Never point anything at mainnet.
2. **Never commit secrets.** Operator keys, custodial keys and the encryption secret live in `.env` (which is git-ignored). Provide `.env.example` with placeholder values. Never print private keys in logs.
3. **All Hedera calls go through `app/hedera.py`.** Nowhere else talks to the SDK directly.
4. **The canonical signed string is a contract:**
   `VERDET|v1|{meterId}|{seq}|{tStartMs}|{tEndMs}|{mWh}|{peakMw}|{avgMv}|{avgMa}`
   It is built in exactly one Python place (`gateway/canonical.py`) and must match `firmware/meter/meter.ino` byte for byte. Change both together, bump `v1`, and update the tests.
5. **Integers on the wire.** Energy is in mWh, power in mW, voltage in mV, current in mA. No floats in signed data.
6. **Every reading decision, accepted or rejected, is submitted to the `readings` HCS topic.** Keep HCS messages under 1 KB.
7. **Verification order is fixed:** registered → signature → sequence check → physics → AI.
   - Sequence check: `seq == last_seq` → `DUPLICATE` (the same window resent, i.e. an attempt to claim twice); `seq < last_seq` → `REPLAY` (an old window resent); only `seq > last_seq` continues.
   - `last_seq` advances for **every reading that passes the signature check**, whether it is later accepted or rejected.
   - Database backstop for races: a partial unique index on `(meter_id, seq) WHERE decision = 'accepted'`. A violation is reported as `DUPLICATE`. Rejected readings are stored too.
   - Codes, used exactly: `UNREGISTERED`, `BAD_SIGNATURE`, `REPLAY`, `DUPLICATE`, `PHYSICS_LIMIT`, `NIGHT_GENERATION`, `AI_ANOMALY` (`AI_UNAVAILABLE` is logged, not a rejection).
8. **VKWH:** 1 VKWH = 1 kWh, 6 decimals (1 unit = 1 mWh). Mint only for accepted readings.
9. **The marketplace buy is one atomic `TransferTransaction`** (VKWH and HBAR legs, both signatures). If the SDK can't do this, stop and ask; don't silently use two transfers.
10. **Honest claims only.** No "trustless", no "carbon credits", no "real energy delivery" in the UI, docs or comments. Say "verified-energy reward token" and "trust-minimized and auditable".
11. **Stay in scope.** If something isn't in `Plan.md`, ask before building it. Suggest it for §5 (future ideas) instead.
12. **Finish phases in order.** A phase is done only when its checkpoint in `Plan.md` passes. Exception: the Mega signing spike is split off from Phase 0. The Hedera half of Phase 0 gates Phase 1; the Mega signing check gates **Phase 4 only** (the board arrives around Oct 14). Until then the simulator signs with PyNaCl using the same canonical string.

## Code style
- Simple and readable over clever. Type hints on functions. Short docstrings.
- Small functions, plain names. No heavy abstractions, no extra frameworks.
- Handle Hedera errors with clear messages that are shown in the UI (e.g. "Hedera is slow, try again").
- Every rule in `verify.py` and `ai.py` gets a pytest test, including the failure case.

## Hedera references
- Docs: https://docs.hedera.com (the Hedera Docs MCP server is at `https://docs.hedera.com/mcp`)
- Python SDK source and examples: the `hiero-ledger/hiero-sdk-python` GitHub repo (`examples/` is the best reference)
- HashScan testnet links: `https://hashscan.io/testnet/transaction/{txId}`, `/topic/{topicId}`, `/token/{tokenId}`, `/account/{accountId}`
- Mirror node (testnet): `https://testnet.mirrornode.hedera.com/api/v1/`
- Check SDK method names against the current SDK examples; don't guess from the JavaScript SDK.
- The **operator** key comes from the Hedera portal and may be ECDSA or Ed25519. Parse it with the type-specific loader that matches the portal account; don't rely on auto-detection. **User** account keys VERDET creates are always Ed25519.
- New users get 10 HBAR from the operator. Keep an eye on the operator's testnet balance.

## Settled implementation details
- Custodial keys are encrypted with Fernet (`cryptography`), keyed by `KEY_ENCRYPTION_SECRET`.
- Treasury, token admin key and supply key = operator. Topics have `submit_key` = operator.
- Routes are plain `def` (the SDK is synchronous). No migrations: `create_all()` at startup; delete `verdet.db` to reset locally.
- Postgres driver `psycopg[binary]` is added in Phase 7, not before.
- Branch: `main`. Commit at every passing checkpoint.
