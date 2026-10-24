# VERDET: Verifiable Decentralized Energy Trading

**Verified solar energy, rewarded and tradable on Hedera.**

A meter signs every reading of a solar panel's output. VERDET checks each reading, records every decision on Hedera, and pays accepted energy in **VKWH**, a verified-energy reward token (1 VKWH = 1 kWh). Owners can **trade VKWH for HBAR** with other users in a single atomic Hedera transaction, or **redeem it for airtime and data**.

- **Live app:** https://verdet.onrender.com (Hedera testnet)
- **Public impact page (no login):** https://verdet.onrender.com/impact


> The live app runs on a free hosting tier during development. If it has been idle, the first page can take up to a minute to load.

---

## Track alignment

|Tech Track theme | What VERDET does |
|---|---|
| **Peer-to-peer energy trading** | Users list verified energy (VKWH) at a price per kWh; buyers pay in HBAR. Each purchase is **one** Hedera `TransferTransaction` carrying both legs: the VKWH and the HBAR move together or not at all. |
| **Tokenized incentives for clean energy** | Every accepted reading mints VKWH to the meter owner. VKWH can be spent on airtime and data (GreenAfrica-style rewards); the spent VKWH is returned to the treasury and **burned**. |
| **Trust and transparency** | Every reading decision, accepted **or rejected**, is written to a public Hedera Consensus Service topic. Anyone can audit it on HashScan. |

---

## The problem

Across Nigeria and much of sub-Saharan Africa, homes and small businesses are adding small solar systems to escape unreliable grid power and expensive diesel generators. Their clean generation is invisible:

- **No proof.** Small producers have no trustworthy record of what their panels actually produced.
- **No reward.** Without proof, the clean energy can't be rewarded, financed or traded.
- **Self-reported data invites fraud.** Incentive schemes that rely on self-reported numbers, or on a PC that can edit them, are easy to game.

## The solution

1. **The meter signs.** An Arduino-based meter measures the panel's voltage and current, adds up the energy in 60-second windows, and signs each window with its own Ed25519 key. The PC gateway only relays readings; it holds no key, so it can drop a reading but can't forge or change one.
2. **VERDET verifies.** Each reading runs four checks in a fixed order. Fakes are rejected with an exact reason code:

   | Check | Rejection code | What it stops |
   |---|---|---|
   | Registered | `UNREGISTERED` | Readings from unknown devices |
   | Signature | `BAD_SIGNATURE` | Readings edited after signing, or signed by another key |
   | Sequence | `REPLAY` / `DUPLICATE` | Old readings resent, or one reading claimed twice |
   | Physics | `PHYSICS_LIMIT` | Output above 1.1 × the panel's rating, or more energy than its peak power allows |

3. **Hedera records.** Every decision goes to the `readings` HCS topic with the reading's SHA-256 hash, so the record is public, timestamped and tamper-evident.
4. **Earn, trade, redeem.** Accepted energy mints VKWH (Hedera Token Service) to the meter owner. VKWH can be sold peer-to-peer for HBAR or redeemed for airtime and data.

Every page links to its proof on HashScan. On any reading's detail page, **"Verify in your browser"** rebuilds the signed text and checks the meter's Ed25519 signature and the SHA-256 hash locally, without trusting the VERDET server.

## How it works

```
 Solar panel ──▶ ACS712 current sensor ──▶ DC load
      │ voltage divider
 ┌────▼──────────────────────────────┐
 │ METER: Arduino Mega               │  sample V and I (1 kHz), 60 s windows,
 │ Ed25519 key + sequence in EEPROM  │  sign each window, print JSON
 └────┬──────────────────────────────┘
      │ USB serial
 ┌────▼──────────────────────────────┐
 │ GATEWAY (PC): serial_bridge.py    │  relays unchanged; holds no key
 └────┬──────────────────────────────┘
      │ HTTPS POST /api/readings
 ┌────▼─────────────────────────────────────────────────────┐
 │ VERDET APP (FastAPI)                                     │
 │  verify: registered → signature → sequence → physics     │
 │  record: every decision → HCS `readings` topic           │
 │  reward: accepted → mint VKWH → owner (HTS)              │
 │  trade:  one atomic VKWH⇄HBAR transfer → `trades` topic  │
 │  redeem: VKWH → treasury → burn → `redemptions` topic    │
 └────┬───────────────────────────────┬─────────────────────┘
      │                               │
 Browser / installable PWA       Hedera testnet + mirror node + HashScan
```

**Hedera services used**

| Service | Use |
|---|---|
| **Accounts** | Every user gets a real Hedera account at sign-up; the App ID is the account ID |
| **Consensus Service (HCS)** | Four topics: `registry` (meters), `readings` (every decision), `trades`, `redemptions` |
| **Token Service (HTS)** | The VKWH fungible token: mint for accepted readings, transfer to owners, burn on redemption |
| **Atomic transfers** | One `TransferTransaction` with a token leg and an HBAR leg, signed by buyer and seller |
| **Mirror node** | Balances, and reading back HCS messages on each reading's detail page |

## Target users

- **Owners of small solar systems:** homes, shops, schools and clinics with a few hundred watts to a few kilowatts of panels, who want their clean generation recognised and rewarded.
- **Solar companies (pay-as-you-go and installers):** a verified, auditable record of what their installed systems produce, and an incentive layer for their customers.
- **Mini-grid operators:** per-site verified generation for reporting and financing.
- **Reward sponsors:** telecom companies, NGOs and climate programmes that want to fund clean-energy rewards (airtime, data) and see exactly what they paid for.
- **Buyers of verified clean energy:** businesses and individuals who want to buy the verified attribute of clean generation from peers.

## How it scales

- **Per-reading cost is small.** One HCS message (about US$0.0001) per reading, plus mint and transfer fees only when a reading is accepted.
- **Stateless verification.** Each check is a pure function of the reading and the meter's registration, so the app scales horizontally behind a load balancer, with Postgres as the shared state.
- **Hedera throughput.** Thousands of meters reporting once a minute is well within HCS capacity. For larger fleets, decisions can be batched into Merkle roots: one HCS message per batch, still verifiable per reading.
- **Cheaper, connected meters.** The same signing scheme runs on an ESP32 with Wi-Fi or GSM, removing the PC gateway.
- **Beyond small DC systems.** For grid-tied and mini-grid sites, a certified AC energy-metering chip replaces the sensor front-end. The signed format, verification, Hedera records and token stay the same, because everything works in mWh.

## How easily it can be adopted

- **No crypto wallet needed.** Sign up with email and password; you instantly get a Hedera account (your App ID) with a small HBAR balance. No seed phrases or browser extensions.
- **Installable on a phone.** The web app is a PWA: "Add to Home Screen" installs it like an app.
- **Low-cost hardware.** An Arduino-class board, a current sensor and a few resistors, retrofitted next to an existing panel without changing the installation.
- **Familiar rewards.** Airtime and data, which every mobile user already understands.
- **Open, checkable records.** Every claim links to HashScan, so partners and sponsors can audit without trusting VERDET.

## Try it

**On the live app:**
1. Open https://verdet.onrender.com and **sign up**. You get a real Hedera testnet account with 10 HBAR.
2. Visit **Market** to see offers, and **Impact** for network-wide numbers and the latest proofs on Hedera.
3. To see a meter's readings, VKWH balance and rewards, log in as the demo seller below.


## Run it locally

Requirements: Python 3.11 or later, and a Hedera **testnet** account from [portal.hedera.com](https://portal.hedera.com).

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env              # then fill in the operator account and secrets (see comments inside)
python scripts/setup_hedera.py    # creates the topics and the VKWH token; paste the printed IDs into .env

uvicorn app.main:app --reload     # http://127.0.0.1:8000
```

**Send readings without hardware** (the simulator signs exactly like the meter):
```bash
python gateway/simulator.py --show-key                      # register this ID and key on the Meter page
python gateway/simulator.py --fast --count 10 --hour 12     # 10 signed midday readings
python gateway/simulator.py --spike --count 1               # an impossible reading → PHYSICS_LIMIT
```

**Try the attacks** (each prints PASS when rejected with exactly its code):
```bash
python gateway/attacks/unregistered.py     # → UNREGISTERED
python gateway/attacks/tamper.py           # → BAD_SIGNATURE
python gateway/attacks/replay.py           # → REPLAY
python gateway/attacks/duplicate.py        # → DUPLICATE
```

**Real meter:** flash `firmware/meter/meter.ino` to an Arduino Mega (library: "Crypto" by Rhys Weatherley), register the `PUB:` key it prints, then:
```bash
python gateway/serial_bridge.py --port COM9 --url http://127.0.0.1:8000
```

**Tests:**
```bash
pytest        # 218 tests; Hedera is faked, so no testnet funds are used
```

## Testnet IDs and proof

| Item | ID |
|---|---|
| VKWH token | [`0.0.11006043`](https://hashscan.io/testnet/token/0.0.11006043) |
| `registry` topic | [`0.0.11006039`](https://hashscan.io/testnet/topic/0.0.11006039) |
| `readings` topic | [`0.0.11006041`](https://hashscan.io/testnet/topic/0.0.11006041) |
| `trades` topic | [`0.0.11006042`](https://hashscan.io/testnet/topic/0.0.11006042) |
| `redemptions` topic | [`0.0.11090339`](https://hashscan.io/testnet/topic/0.0.11090339) |
| Operator / treasury account | [`0.0.7179539`](https://hashscan.io/testnet/account/0.0.7179539) |

**Example transactions**

- **A reading signed by the real Arduino meter**, accepted and recorded: [`0.0.7179539@1792403438.956951379`](https://hashscan.io/testnet/transaction/0.0.7179539-1792403438-956951379)
- **An atomic trade** (VKWH and HBAR in one transaction): [`0.0.7179539@1792402780.213973760`](https://hashscan.io/testnet/transaction/0.0.7179539-1792402780-213973760)
- **A redemption:** VKWH returned to the treasury [`…1792512441.809444665`](https://hashscan.io/testnet/transaction/0.0.7179539-1792512441-809444665), burned [`…1792512447.012866258`](https://hashscan.io/testnet/transaction/0.0.7179539-1792512447-012866258), recorded [`…1792512447.721848726`](https://hashscan.io/testnet/transaction/0.0.7179539-1792512447-721848726)

## Honest limits

- **Testnet only.** No real money; all accounts and tokens are on Hedera testnet.
- **Prototype hardware.** The meter is an uncalibrated breadboard prototype (Arduino Mega, ACS712-20A, voltage divider), DC only. 
- **Trust-minimized, not trust-free.** A signature proves which device sent a reading, not that sunlight produced it. Someone with physical access could feed the sensor from another power source, or extract the key from the board's memory. Production meters would need a secure element, a tamper-evident enclosure, installer attestation and spot audits.
- **No AI or weather cross-check yet.** Readings are checked for identity, integrity, sequence and physical limits, not compared with sunlight data.
- **Custodial accounts.** VERDET holds users' Hedera keys (encrypted at rest) so they don't need a wallet. Non-custodial login is future work.
- **VKWH is a verified-energy reward token.** It is not a certified renewable-energy certificate or offset, and trading it moves the verified attribute, not electricity.
- **Rewards are demo vouchers.** Redeeming really returns and burns VKWH on Hedera, but airtime and data are not actually delivered, and the rates are demo rates.
- **Impact figures are estimates.** Diesel and CO₂ avoided use configurable factors (0.3 L/kWh, 2.68 kg CO₂/L), shown with their formula on the impact page.
- **Open endpoint.** `/api/readings` needs no login (the signature is the credential), so junk readings cost an HCS fee each; rate limiting is future work.

## Tech stack

Python (FastAPI, SQLModel, Jinja2, HTMX, Tailwind, Chart.js) · Hiero Python SDK for Hedera · PyNaCl (Ed25519) · argon2 · Postgres (Neon) in production, SQLite locally · Arduino C++ with the rweather Crypto library · deployed on Render.

## Repository layout

```
app/        FastAPI app: routes, models, Hedera calls (hedera.py), verification (verify.py), templates, PWA files
gateway/    canonical signed format, simulator, serial bridge, attack scripts
firmware/   Arduino Mega meter firmware (meter/) and the signing spike (spike/)
scripts/    one-time Hedera setup, SDK spike, password reset
tests/      pytest suite
docs/       Hedera SDK notes
```
