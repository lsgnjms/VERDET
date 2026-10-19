# VERDET — Build Plan (v2)

**VER**ifiable **D**ecentralized **E**nergy **T**rading
IEEE ClimateChain Global Hackathon · **Track: Renewable Energy & Energy Trading**
Deadline: **Sun Oct 25, 2026, 3:00 pm GMT+1 (Lagos)**. Eligibility and the submission window have been confirmed with the organizers.
Budget: **10 days × 4 h = 40 h**. Day 1 = Mon Oct 12 → Day 10 = Wed Oct 21. Oct 22–24 is buffer.

**Changes from v1:** Python end to end (FastAPI) instead of Next.js/TypeScript. The AI verifier now runs inside the main app, not as a separate service. Trading is cut to a single feature: a fixed-price marketplace with atomic swaps. Login is locked as email and password. Eligibility has been checked against the rules.

---

## 1. Overview

### Goal
A working, installable PWA where a user:

1. **Signs up with email and password** and immediately receives an **App ID that is their Hedera account ID** (e.g. `0.0.5123456`).
2. **Registers a meter**: an Arduino Mega that measures solar output and **signs every reading with its own key**.
3. **Tracks power**: a live power chart, today's verified energy, and every reading's accept/reject decision linked to HashScan.
4. **Earns VKWH**, a Hedera token minted only for *verified* solar energy (1 VKWH = 1 kWh, 6 decimals = 1 mWh resolution).
5. **Trades** VKWH on a marketplace: list X VKWH at Y HBAR. A buyer purchases in **one atomic Hedera transaction**: both sides move together or neither does.

A **public impact page** shows total verified kWh, estimated diesel displaced and CO₂ avoided, and links to the on-chain proof. No login is needed.

**Precedent:** GreenAfrica (winner, 2025 Hedera Africa Hackathon). The pattern is: a device captures something real → verification rejects fakes → Hedera records proof → the user gets a reward → a public dashboard shows impact.
**VERDET's additions:** the meter signs its own readings; an AI and physics gate; every rejection is recorded on-chain; peer-to-peer trading via atomic swap.

### Non-goals
- **Physical electricity delivery.** Trades move the *verified green-energy attribute* (like a renewable certificate), not electrons. The README and demo will say so.
- **Real carbon credits or RECs.** VKWH is a prototype token aligned with certificate concepts.
- **Mainnet or real money.** Everything runs on Hedera **testnet**.
- **"Trustless" claims.** VERDET is *trust-minimized and auditable*, and its limits are on a threat-model slide.
- **AC or mains metering.** The prototype is low-voltage DC only (a small panel and a DC load).
- **Extra features.** There is no separate send/gift feature, no wallet-connect, and no smart contracts. Each was cut to keep the build lean.

### Assumptions
- Solo entry. The rules allow individual submissions and independent developers.
- Panel rating and load are **TBD**. Software is built against a **simulated meter** with the identical signed format, so hardware never blocks progress.
- You're new to Python backends. Code is delivered in small pieces, and each phase ends with a check you can run yourself.

---

## 2. Architecture and key decisions

```
 Solar panel ──▶ Load (DC bulb/fan) ──▶ shunt ──▶ back to panel (−)
     │ divider
 ┌───▼─────────────────────────────┐
 │ METER: Arduino Mega  firmware/  │ sample V, I every 1 s → P = V·I
 │ Ed25519 key + seq in EEPROM     │ accumulate mWh per window → sign
 └───┬─────────────────────────────┘
     │ USB serial (signed JSON lines)
 ┌───▼─────────────────────────────┐
 │ GATEWAY: PC  gateway/ (Python)  │ relays only; it has no key, so it
 │ + simulator + attack scripts    │ can drop readings but cannot forge them
 └───┬─────────────────────────────┘
     │ HTTPS POST /api/readings
 ┌───▼──────────────────────────────────────────────────────┐
 │ VERDET APP: FastAPI (one Python service)  app/           │
 │  Pages: Jinja2 + HTMX + Tailwind + Chart.js (PWA)        │
 │  Verify: registered? → signature → replay → duplicate    │
 │          → physics → AI (pvlib + IsolationForest)        │
 │  Record: every decision → HCS `readings` topic           │
 │  Reward: accepted → mint VKWH → transfer to owner (HTS)  │
 │  Trade:  offer → atomic HBAR⇄VKWH swap → `trades` topic   │
 │  DB: SQLite (local) / Postgres (production)              │
 └───┬───────────────────────────────┬──────────────────────┘
     │                               │
 Browser / installed PWA        Hedera testnet: HCS topics, VKWH token,
                                mirror node (balances), HashScan (proof)
```

### Repo layout
```
verdet/
  app/
    main.py            FastAPI app, routes
    models.py          SQLModel tables
    hedera.py          all Hedera calls in one place
    verify.py          signature, replay, physics checks
    ai.py              pvlib clear-sky + IsolationForest
    templates/         Jinja2 pages
    static/            manifest.json, sw.js, icons, css/js
  gateway/
    simulator.py  serial_bridge.py  canonical.py  attacks/
  firmware/meter/meter.ino
  scripts/  spike.py  setup_hedera.py
  tests/
  docs/
```

### Key decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **Python end to end: FastAPI + the official Hiero/Hedera Python SDK.** The only non-Python code is the Arduino sketch and a small service-worker JS file. | You're stronger in Python. FastAPI is the easiest Python backend to learn, and it can do everything the Next.js backend would have done. One language means less context-switching across 40 h. |
| D2 | **Pages are server-rendered (Jinja2) with HTMX**, not a React frontend. | A PWA only needs a web manifest and a service worker, and any website can have those. HTMX gives live-updating panels (e.g. polling every 5 s) without writing a JavaScript app. |
| D3 | **One deployable service.** The AI verifier is a Python module inside the app (`ai.py`). | v1 had two services. One service halves deployment work and failure points. |
| D4 | **HCS + HTS native services only. No Guardian, no Solidity.** | This matches GreenAfrica's winning stack (HCS, HTS, web app). Hedera's native token and consensus services do what a contract would do here, more cheaply. The README explains this for the "smart contracts" judging line. |
| D5 | **The Arduino Mega is the meter and signs with Ed25519** (rweather `Crypto` library). The key is generated on first boot and stored in EEPROM. | Device signing is the oracle defence: the PC gateway can't invent readings. Hedera also uses Ed25519. |
| D6 | **Canonical signed string:** `VERDET\|v1\|{meterId}\|{seq}\|{tStartMs}\|{tEndMs}\|{mWh}\|{peakMw}\|{avgMv}\|{avgMa}` | It is easy to rebuild byte-for-byte in C and in Python. JSON field order isn't reliable for signatures. |
| D7 | **Device sequence numbers + Hedera consensus timestamps.** | `seq` persists in EEPROM and blocks replay. Hedera's consensus timestamp is the authoritative time, so no RTC is needed. |
| D8 | **Double-count guard: database `UNIQUE(meter_id, seq)` + HCS audit log.** | It is simple, and anyone can re-derive it from the public topic. |
| D9 | **Every decision, including rejections, goes to HCS.** Messages stay under 1 KB. | On-chain rejections prove the verification is real, not decorative. |
| D10 | **Email + password login with a custodial testnet account.** On signup the app creates a Hedera account (with automatic token associations and a small starting HBAR balance so the user can buy), and encrypts its key at rest. | This gives GreenAfrica-style onboarding: no wallet app, and the App ID is the Hedera ID. Custody is acceptable on testnet. |
| D11 | **Trading = fixed-price offers + one atomic `TransferTransaction`** (VKWH seller→buyer and HBAR buyer→seller, signed by both keys). | It is the smallest feature that is genuinely "peer-to-peer energy trading", and the single-transaction swap is visible on HashScan, which is the stand-out moment. |
| D12 | **Database: SQLModel; SQLite locally, Postgres in production (Neon free tier).** | There's no database server to install while developing. Changing one environment variable switches to production. |
| D13 | **Deploy on Render** (free web service). | It's a simple Python deploy. Cold starts are handled in Risk R8. |

### Stack summary
| Part | Tools |
|---|---|
| Web app / API | FastAPI, Uvicorn, Jinja2, HTMX, Tailwind (CDN), Chart.js |
| Auth | argon2 password hashing, signed session cookies |
| Database | SQLModel → SQLite / Postgres |
| Hedera | Hiero Python SDK (HCS, HTS, accounts), mirror node REST via `httpx` |
| Crypto | PyNaCl (Ed25519 verify), `cryptography` (encrypting custodial keys) |
| AI | pvlib (clear-sky solar estimate), scikit-learn (IsolationForest) |
| Gateway | pyserial |
| Tests | pytest |
| Meter | Arduino Mega, rweather `Crypto` library |

### HCS message schemas (v1)
**`registry`**
```json
{"t":"meter.register","v":1,"meterId":"VD-0001","owner":"0.0.5123456","pubKey":"<hex>","ratedW":10,"lat":6.52,"lon":3.38}
```
**`readings`**
```json
{"t":"reading","v":1,"meterId":"VD-0001","seq":42,"mWh":167,"peakMw":10120,"hash":"<sha256 of canonical string>","decision":"accepted","reasons":[],"aiScore":0.12,"mintTx":"0.0.x@..."}
{"t":"reading","v":1,"meterId":"VD-0001","seq":41,"hash":"...","decision":"rejected","reasons":["REPLAY"]}
```
**`trades`**
```json
{"t":"trade","v":1,"offerId":19,"seller":"0.0.a","buyer":"0.0.b","vkwh":"0.500000","hbar":"2","tx":"0.0.x@..."}
```
Rejection codes: `UNREGISTERED`, `BAD_SIGNATURE`, `REPLAY`, `DUPLICATE`, `PHYSICS_LIMIT`, `AI_ANOMALY`.

---

## 3. Phases

Don't start the next phase until the current checkpoint passes.

### Phase 0 — Prove the risky parts (Day 1, 4 h)
**Goal:** Prove that the Python SDK can do every Hedera operation VERDET needs and that the Mega can sign, before building anything else.

- [ ] Install Python 3.11+, create the repo and virtual environment, `pip install` the SDK, PyNaCl and pyserial; push to a public GitHub repo
- [ ] Create a testnet account in the Hedera portal; put the operator ID and key in `.env`
- [ ] `scripts/spike.py`: create topic → submit message → create VKWH token → mint → create two accounts (with auto-association + starting HBAR) → **atomic swap** (VKWH one way, HBAR the other, two signatures, one transaction)
- [ ] Mega spike: generate a keypair, sign the test string, print the pubkey and signature over serial
- [ ] `gateway/verify_test.py`: PyNaCl verifies the Mega's signature

**Deliverables:** repo, `scripts/spike.py`, `firmware/spike/spike.ino`, `gateway/verify_test.py`
**Checkpoint:**
- `python scripts/spike.py` prints topic, token, account and transaction IDs, and each opens on hashscan.io (testnet)
- The swap appears on HashScan as **one** transaction with both a token transfer and an HBAR transfer
- The Mega's signature verifies `True`; changing one character of the string → `False`

**Effort:** 4 h

---

### Phase 1 — Sign up, get your App ID (Day 2, 4 h)
**Goal:** A new user signs up and instantly has a real Hedera account shown as their App ID.

- [ ] FastAPI skeleton, base template (Tailwind, HTMX), `models.py` (`User`, `Meter`, `Reading`, `Offer`)
- [ ] Sign-up / log-in / log-out with argon2 and session cookies
- [ ] On sign-up: `AccountCreateTransaction` (auto associations, starting HBAR) → store the encrypted key → App ID = account ID
- [ ] `scripts/setup_hedera.py`: create the `registry`, `readings` and `trades` topics and the VKWH token once; save the IDs to `.env`
- [ ] Wallet card: App ID, HBAR and VKWH balances from the mirror node, HashScan link

**Deliverables:** `app/main.py`, `models.py`, `hedera.py`, auth pages, wallet card, `setup_hedera.py`
**Checkpoint:** `uvicorn app.main:app --reload` → sign up two users in two browsers. Each sees a different `0.0.x` App ID that opens on HashScan, and the database holds only encrypted keys. `pytest tests/test_auth.py` passes.
**Effort:** 4 h

---

### Phase 2 — Meters and signed readings, using the simulator (Day 3, 4 h)
**Goal:** A registered (simulated) meter streams signed readings, and the app rejects unregistered, tampered, replayed and duplicate ones.

- [ ] "Add meter" page: meter ID, pubkey (printed by the device), rated watts, location → database + `registry` HCS message
- [ ] `gateway/canonical.py`: builds the D6 string (one shared function for the simulator and the app's verifier)
- [ ] `gateway/simulator.py`: keypair, realistic Lagos solar curve, 1-minute windows (`--fast` = one every 5 s), signs and POSTs
- [ ] `POST /api/readings`: registered → signature → `seq > last` → unique `(meter, seq)`
- [ ] `gateway/attacks/`: `unregistered.py`, `tamper.py`, `replay.py`, `duplicate.py`

**Deliverables:** meter registration page, `/api/readings` (checks 1–4), simulator, attack scripts
**Checkpoint:**
- `python gateway/simulator.py --fast` → readings stored as `accepted`
- Each attack script returns exactly its code: `UNREGISTERED`, `BAD_SIGNATURE`, `REPLAY`, `DUPLICATE`
- `pytest tests/test_verify.py` passes

**Effort:** 4 h

---

### Phase 3 — Physics rules, on-chain proof, rewards (Day 4, 4 h)
**Goal:** Every decision is anchored on Hedera, and accepted energy mints VKWH to the meter's owner.

- [ ] Physics checks: `peak ≤ 1.1 × ratedW`, `mWh ≤ peak × window` (night check dropped Oct 14: daylight demo; evening hardware testing must work)
- [ ] Every decision → `readings` topic; store the transaction ID
- [ ] Accepted → mint `mWh / 1e6` VKWH to the treasury → transfer to the owner
- [ ] Readings table (HTMX auto-refresh): time, energy, decision, reasons, HashScan links
- [ ] Simulator flag `--spike`

**Deliverables:** full rules pipeline, HCS anchoring, mint + transfer, readings table
**Checkpoint:** 10 simulated windows → 10 messages visible on the topic in HashScan, and the owner's VKWH balance equals the sum of accepted mWh. `--spike` is rejected, and that rejection is on HCS too.
**Effort:** 4 h

---

### Phase 4 — The real meter (Day 5, 4 h)
**Goal:** The Arduino Mega replaces the simulator and real, signed measurements flow end to end.

- [ ] Circuit (updated Oct 19 for the parts available): 47 kΩ/10 kΩ voltage divider on A0, ACS712-20A current sensor on A1 in series with the load, 12 V fan + 20–22 Ω 10 W resistor as the load, LM2596 buck on the panel, common ground
- [ ] Firmware: 1 kHz timer-interrupt sampling (so signing, about 6.3 s, doesn't pause measurement), 1-second averages, accumulate mWh, keep `seq` and the key in EEPROM, sign per window, print JSON lines
- [ ] Calibrate V and I against a multimeter and store the constants in firmware
- [ ] `gateway/serial_bridge.py`: read serial lines → POST to `/api/readings`
- [ ] Register the real meter using the pubkey printed on first boot

**Deliverables:** `firmware/meter/meter.ino`, `docs/wiring.png`, `gateway/serial_bridge.py`, calibration notes
**Checkpoint:**
- V and I are within ±5% of the multimeter at two load points
- Real readings show `accepted` in the app and mint VKWH
- Unplug and replug the Mega → `seq` continues, and replaying an old line → `REPLAY`

**Effort:** 4 h

---

### Phase 5 — AI verification (Day 6, 4 h)
**Goal:** An AI check catches plausible fakes that the rules miss, and the app keeps working if the check fails.

**Scope cut (Oct 19, R13 fallback): minimal version only.** No IsolationForest / scikit-learn model.
- [ ] `ai.py`: pvlib clear-sky expected energy for the meter's location, window and rating; flag energy well above what the sky allows → `AI_ANOMALY`
- [ ] Wire it into the pipeline after the physics checks; if it errors → rules-only, logged as `AI_UNAVAILABLE`
- [ ] Simulator flag `--lamp` (steady output when the sky can't provide it)
- [ ] `tests/test_ai.py`: normal accepted; lamp flagged
- [ ] Open decision: the check rejects evening bench tests (clear sky = 0 at night). Proposed `.env` switch to disable it for bench testing. Label the step honestly in the UI ("Solar model", not "AI")

**Deliverables:** `app/ai.py`, tests, `AI_ANOMALY` decisions on HCS
**Checkpoint:** `pytest tests/test_ai.py` passes. `--lamp` → `AI_ANOMALY` in the app and on HCS, and normal data is still accepted.
**Effort:** 4 h

---

### Phase 6 — Marketplace with atomic swaps (Day 7, 4 h)
**Goal:** Users sell verified energy to each other for HBAR in one atomic transaction.

- [ ] `Offer` table: seller, VKWH amount, HBAR price, status (`open` / `filled` / `cancelled`)
- [ ] Marketplace page: list an offer, cancel your own, buy someone else's
- [ ] Buy = one `TransferTransaction` (VKWH seller→buyer, HBAR buyer→seller), signed by both custodial keys → `trades` HCS message
- [ ] Guards: can't list more than your balance; can't buy your own offer; an offer is locked while a purchase is in progress
- [ ] Trade history with HashScan links

**Deliverables:** marketplace page, `POST /offers`, `POST /offers/{id}/buy`, `POST /offers/{id}/cancel`
**Checkpoint:** User A lists 0.5 VKWH for 2 HBAR and User B buys it. **One** transaction on HashScan shows both legs, both balances update, and the offer shows `filled`. A second buy attempt fails with a clear message.
**Effort:** 4 h

---

### Phase 6b — Price per kWh + redeem VKWH for airtime/data (added Oct 19)
**Goal:** sellers price intuitively, and VKWH can be spent on everyday rewards (GreenAfrica-style tokenized incentives), alongside P2P trading.

- [ ] Sell form: amount + **price per kWh** (pre-filled suggested price, Market/±5% buttons, slider within the band); total computed exactly; minimum offer size
- [ ] Rewards page: catalogue (airtime / data, Nigerian networks) priced in VKWH at **demo rates**
- [ ] Redeem = user's VKWH → treasury (signed by the user's custodial key) → **burned** (TokenBurn, supply key), recorded on a new `redemptions` HCS topic (phone number masked)
- [ ] Delivery is a **demo voucher**, clearly labelled "testnet demo: airtime/data not actually delivered" (option A; real sandbox delivery is a §5 idea)
- [ ] Guards: enough available VKWH (balance minus VKWH listed in offers), valid phone and network

**Checkpoint:** a user redeems an item; HashScan shows the VKWH transfer to the treasury, the burn, and the `redemptions` message; the voucher appears in their history.

---

### Phase 7 — PWA, impact page, deployment (Day 8, 4 h)
**Goal:** A polished, installable app on a public URL that judges can open.

- [ ] Live power chart (Chart.js, refreshed every 5 s) and today's verified kWh on the dashboard
- [ ] Public `/impact` page: total verified kWh, diesel litres displaced and CO₂ avoided (cited, configurable factors with the formula shown), active meters, latest HCS proofs
- [ ] Public landing page at `/` (added Oct 19): hero, how it works in 4 steps, live impact numbers, sign-up button. UI refresh per `docs/ui-plan.md`
- [ ] `manifest.json`, icons and `sw.js` (caches the app shell) → installable on a phone
- [ ] Deploy to Render with Postgres on Neon; set environment variables; point the gateway at production
- [ ] Mobile layout pass

**Deliverables:** dashboard, `/impact`, landing page, PWA files, live URL
**Checkpoint:** The production URL installs as an app on your phone. The bridge running against production moves the chart on the phone, and `/impact` loads logged-out with working HashScan links.
**Effort:** 4 h

---

### Phase 8 — Hardening, documentation, rehearsal (Day 9, 4 h)
**Goal:** Everything a judge reads or clicks is complete and correct.

- [ ] Full run on production: sign up → register meter → generate → mint → sell → impact page
- [ ] `README.md` covering the submission requirements: track alignment, problem, solution, **target users**, **how it scales**, **how easily it can be adopted**, how to run it, testnet IDs, honest limits
- [ ] `docs/architecture.png`, `docs/threat-model.md` (lamp, hardware tampering, gateway censorship → secure element, tamper-evident enclosure, installer attestation, spot audits)
- [ ] Demo script (3–5 min): climate problem → meter → HCS → token → trade → impact, plus the four on-camera rejections (unregistered, fake spike, replay, duplicate)
- [ ] Rehearse twice and time it

**Deliverables:** README, docs, demo script
**Checkpoint:** A fresh clone following the README runs the simulator flow locally, and the rehearsal runs under 5 minutes with no manual fixes.
**Effort:** 4 h

---

### Phase 9 — Video and submission (Day 10, 4 h)
**Goal:** The entry is complete and ready to submit within the window the organizers confirm.

- [ ] Record the demo video (real hardware, live HashScan, phone PWA) and upload it (YouTube, unlisted)
- [ ] Fill in the Devpost form and **save it as a draft**: track alignment, description, repo, video, live URL, screenshots
- [ ] Check every link logged-out in an incognito window
- [ ] Submit within the window the organizers confirmed

**Deliverables:** video, completed Devpost submission
**Checkpoint:** The Devpost page shows "Submitted" and every link works logged-out.
**Effort:** 4 h

**Total: 40 h.**

---

## 4. Risks and unknowns

| # | Risk / unknown | Plan |
|---|---|---|
| R1 | ~~Eligibility~~ | **Resolved** with the organizers. |
| R2 | ~~Submission window conflict~~ | **Resolved** with the organizers. Still finish by Oct 24 and keep the Devpost draft ready. |
| R3 | **Python SDK gaps.** The Hiero Python SDK is younger than the JS one; the two-signature atomic swap is the most likely gap. | Tested on Day 1 in `spike.py`. Fallback A: two sequential transfers with the offer locked between them (not atomic, stated honestly). Fallback B: one small Node script for the swap only. |
| R4 | **First Python backend.** | FastAPI with small, reviewed code drops; every phase has a runnable checkpoint; no frontend framework to learn. |
| R5 | **Ed25519 on the Mega** fails, is too slow, or uses too much memory. | Tested on Day 1. Fallback: HMAC-SHA256 with a per-meter secret (weaker, since the server shares the secret), stated in the threat model. |
| R6 | **Panel unknown / hardware late.** | Simulator-first design means only Phase 4 depends on hardware. Bring-up works with any DC source + resistor. Size the shunt and divider once the panel rating is known. |
| R7 | **Shunt resolution at low current.** | Use the 1.1 V internal reference, average samples, and pick the shunt value from the panel's current. Last resort: a fixed resistor load with P = V²/R, labelled as calculated. |
| R8 | **Render free tier sleeps** (cold start of about a minute) and Hedera calls can be slow. | Open the URL before demos; note this in the README. If needed, move to a paid starter plan for the judging window only. |
| R9 | **Testnet faucet limits or outage.** | Fund the operator early; record transaction IDs; keep a recorded testnet run for the video. |
| R10 | **Indoor demo trips the clear-sky check.** | Film outdoors in daylight. Use an indoor lamp *deliberately* as an on-camera rejection. |
| R11 | **Diesel and CO₂ figures questioned.** | One cited, conservative factor, configurable, with the formula shown on `/impact`. |
| R12 | **Custodial keys criticised.** | Testnet only, encrypted at rest; non-custodial login is listed in §5. |
| R13 | **Scope creep.** | Anything not in Phases 0–9 goes to §5. If behind on Day 6, Phase 5 is cut to the pvlib check only, without the IsolationForest. |

---

## 5. Out of scope / future ideas
- Direct send/gift of VKWH between users
- Non-custodial login (HashPack, WalletConnect, passkeys)
- Secure element on the meter (ATECC608), tamper-evident enclosure, installer attestation
- Wi-Fi/GSM meter (ESP32 or SIM800) with no PC gateway
- AC metering of inverter output with certified metering chips
- Solidity claims registry; Guardian policy flow; alignment with a named registry methodology (Gold Standard, Verra, I-REC)
- Merkle-batched anchoring for thousands of meters per HCS message
- Real airtime/data delivery through a provider API (e.g. Africa's Talking, Reloadly) and electricity-token credit; demo vouchers were built in Phase 6b
- Isolation-forest anomaly model on top of the clear-sky check (cut from Phase 5 on Oct 19)
- Mini-grid operator dashboard and community energy pools
- Generator-runtime sensing to measure diesel displacement directly
- Night-generation rule (`NIGHT_GENERATION`): reject energy claimed while the sun is down at the meter's location (pvlib solar position). Needs a trusted window time (an RTC on the meter, or Hedera consensus time)
