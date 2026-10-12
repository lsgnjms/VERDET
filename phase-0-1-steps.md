# Phase 0 (Hedera half) + Phase 1: Step Checklist

Save as: `docs/phase-0-1-steps.md`. Tick each box as it's done (ask Claude Code to tick them too).
Source: Claude Code's plan from Oct 12, updated with the decisions settled since (CLAUDE.md rules 7 and 12).

**Goal:** prove the Hedera SDK works on testnet, then build Phase 1: a user signs up and immediately gets a real testnet account as their App ID.

---

## Step 1: Project setup ✅
- [x] `requirements.txt` (fastapi, uvicorn, jinja2, sqlmodel, hiero-sdk-python, httpx, pynacl, cryptography, argon2-cffi, pyserial, pytest, python-dotenv, itsdangerous, python-multipart)
- [x] `.gitignore` (`.env`, `.venv/`, `*.db`, `__pycache__/`)
- [x] `.env.example`
- [x] Branch renamed to `main`; imports check passes; `.env` created and filled in by you
- [ ] First commit + push to GitHub

**Check:** `pip install -r requirements.txt` succeeds; `git status --short` doesn't show `.env` or `.venv`.

## Step 2: `app/hedera.py` (first version)
- [ ] `get_client()`: builds the client from `.env`; refuses to run unless `NETWORK == "testnet"`
- [ ] Parses the **ECDSA** operator key explicitly
- [ ] `create_topic(memo)`, `create_vkwh_token()`
- [ ] `create_user_account()`: new Ed25519 key, 10 HBAR, automatic token association → `(account_id, private_key_string)`
- [ ] `mint_vkwh(...)`, `atomic_swap(...)`
- [ ] `get_balances(account_id)`: mirror node, 5 s timeout, returns `None` on error
- [ ] One error type with a plain message; keys never logged
- [ ] Confirm against the SDK source: the automatic-association setter (fallback: `TokenAssociateTransaction`), key ↔ string conversion, `add_token_transfer` argument order

## Step 3: `scripts/spike.py` ← **Phase 0 checkpoint: STOP here and check**
- [ ] Uses only `hedera.py` functions
- [ ] topic → message → token → mint → two users → treasury sends VKWH to user A → atomic swap (VKWH A→B, HBAR B→A)
- [ ] Prints every ID as a HashScan link

**Check:** `python scripts/spike.py`. Every link opens, and the swap shows as **one** transaction with both a VKWH transfer and an HBAR transfer.

## Step 4: `scripts/setup_hedera.py`
- [ ] Creates the `registry`, `readings` and `trades` topics and the VKWH token; prints the `.env` lines
- [ ] Refuses to run if `VKWH_TOKEN_ID` is already set

**Check:** run it, paste the lines into `.env` (and your password-manager note), and open the token on HashScan.

## Step 5: `app/models.py` + database
- [ ] Engine from `DATABASE_URL`; `get_session` dependency
- [ ] Integers for amounts everywhere (mWh, tinybars)
- [ ] `User`: id, email (unique, lowercase), password_hash, account_id (unique), encrypted_key, created_at
- [ ] `Meter`: id, meter_id (unique), owner_id, pubkey_hex, rated_w, lat, lon, last_seq, created_at
- [ ] `Reading`: meter_id, seq, t_start_ms, t_end_ms, mwh, peak_mw, avg_mv, avg_ma, hash, decision, reasons, ai_score, hcs_tx, mint_tx, created_at; **partial unique index on (meter_id, seq) WHERE decision = 'accepted'**
- [ ] `Offer`: seller_id, amount_mwh, price_tinybar, status, buyer_id, tx_id, timestamps

**Check:** a Python one-liner that creates the tables.

## Step 6: `app/auth.py`
- [ ] `hash_password` / `verify_password` (argon2)
- [ ] `encrypt_key` / `decrypt_key` (Fernet)
- [ ] `current_user(request, session)` from the signed session cookie

## Step 7: `app/main.py` + templates
- [ ] SessionMiddleware (`SESSION_SECRET`, SameSite=lax)
- [ ] Templates: `base.html`, `signup.html`, `login.html`, `wallet.html`, `_balances.html`
- [ ] `GET /` → `/wallet` or `/login`
- [ ] `/signup`: validates input; checks the email is free **before** calling Hedera; on Hedera failure shows a friendly error and saves no user
- [ ] `/login` (generic error on failure), `POST /logout`
- [ ] `/wallet`: App ID, HashScan link, copy button
- [ ] `/wallet/balances`: HTMX partial, refreshes every 10 s; "Balance not available yet" while the mirror node catches up

**Check:** `uvicorn app.main:app --reload`, then sign up.

## Step 8: `tests/test_auth.py`
- [ ] Temporary SQLite database; Hedera calls mocked (no network)
- [ ] Signup creates a user and redirects
- [ ] The stored key is encrypted and decrypts back
- [ ] The password is hashed
- [ ] A duplicate email is rejected without calling Hedera
- [ ] Hedera failure → no user saved, friendly message shown
- [ ] Login with the right password works; the wrong one fails
- [ ] Logout clears the session
- [ ] `/wallet` redirects when logged out

**Check:** `pytest tests/test_auth.py`

---

## Phase 1 checkpoint (all must pass before Phase 2)
- [ ] Two users signed up (normal + incognito window) → different `0.0.x` App IDs, HashScan links open, balances show 10 HBAR and 0 VKWH
- [ ] The database holds only Fernet-encrypted keys (they start with `gAAAA`); check with a Python one-liner, since `sqlite3` may not exist on Windows
- [ ] `pytest tests/test_auth.py` passes
- [ ] `git status` doesn't show `.env` or `verdet.db`
- [ ] Commit + push

## Deferred
- [ ] **Mega signing spike**: when the board arrives (around Oct 14); gates Phase 4 only. Optional early test on wokwi.com.
- [ ] `psycopg[binary]` for Postgres: Phase 7
