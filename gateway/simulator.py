"""Simulated VERDET meter: signs readings exactly like the Mega will, and POSTs them.

Each window is 60 s of one-second samples (V, I) from a simple Lagos solar curve,
added up into integer mWh / mW / mV / mA and signed with the meter's Ed25519 key.
The key and the sequence counter live in gateway/.sim/<meterId>.json (git-ignored),
the way the Mega keeps them in EEPROM.

Usage:
  python gateway/simulator.py --show-key          # print the public key to register on /meters
  python gateway/simulator.py --fast              # one window every 5 s (time-compressed)
  python gateway/simulator.py --fast --count 10 --hour 12   # 10 windows of midday sun
"""

import argparse
import json
import math
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from nacl.signing import SigningKey

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so "gateway" imports

from gateway.canonical import canonical_string  # noqa: E402

SIM_DIR = Path(__file__).resolve().parent / ".sim"
LAGOS = timezone(timedelta(hours=1))   # WAT, no daylight saving
SUNRISE_H, SUNSET_H = 6.75, 19.0       # rough Lagos day length
WINDOW_S = 60                          # seconds of samples per reading
PANEL_VMP_MV = 18_000                  # typical small-panel voltage at max power


# --- Meter identity (key + seq), like the Mega's EEPROM ---------------------

def _state_path(meter_id: str) -> Path:
    return SIM_DIR / f"{meter_id}.json"


def load_meter(meter_id: str) -> dict:
    """Load (or create on first run) the meter's key seed and last used seq."""
    path = _state_path(meter_id)
    if path.exists():
        return json.loads(path.read_text())
    SIM_DIR.mkdir(exist_ok=True)
    state = {"meterId": meter_id, "seed": SigningKey.generate().encode().hex(), "seq": -1}
    save_meter(state)
    return state


def save_meter(state: dict) -> None:
    _state_path(state["meterId"]).write_text(json.dumps(state, indent=2))


def public_key_hex(state: dict) -> str:
    return SigningKey(bytes.fromhex(state["seed"])).verify_key.encode().hex()


def sign(state: dict, reading: dict) -> dict:
    """Return a copy of the reading with its Ed25519 `sig` added."""
    key = SigningKey(bytes.fromhex(state["seed"]))
    signed = dict(reading)
    signed["sig"] = key.sign(canonical_string(reading).encode("utf-8")).signature.hex()
    return signed


# --- Solar model --------------------------------------------------------------

def sun_fraction(hour: float) -> float:
    """0..1: how much of the panel's rating the sun gives at this Lagos hour."""
    if not SUNRISE_H < hour < SUNSET_H:
        return 0.0
    return math.sin(math.pi * (hour - SUNRISE_H) / (SUNSET_H - SUNRISE_H))


def simulate_window(meter_id: str, seq: int, hour: float, rated_w: int) -> dict:
    """60 one-second samples -> one unsigned reading with integer units (rule 5)."""
    clouds = random.uniform(0.85, 1.0)        # slow haze for this window
    total_mw = peak_mw = total_mv = total_ma = 0
    for _ in range(WINDOW_S):
        frac = sun_fraction(hour) * clouds * random.uniform(0.97, 1.03)
        power_mw = int(rated_w * 1000 * frac)
        volt_mv = int(PANEL_VMP_MV * (0.9 + 0.1 * min(frac, 1.0))) if power_mw else 0
        current_ma = power_mw * 1000 // volt_mv if volt_mv else 0
        total_mw += power_mw
        peak_mw = max(peak_mw, power_mw)
        total_mv += volt_mv
        total_ma += current_ma
    t_start_ms = seq * WINDOW_S * 1000        # "ms since boot", like the Mega's millis()
    return {
        "meterId": meter_id,
        "seq": seq,
        "tStartMs": t_start_ms,
        "tEndMs": t_start_ms + WINDOW_S * 1000,
        "mWh": total_mw // 3600,              # sum of mW over 1-s samples = mW·s; /3600 -> mWh
        "peakMw": peak_mw,
        "avgMv": total_mv // WINDOW_S,
        "avgMa": total_ma // WINDOW_S,
    }


# --- Sending ------------------------------------------------------------------

def send(url: str, reading: dict) -> dict:
    """POST one reading to the app. Returns the app's JSON answer."""
    resp = httpx.post(f"{url.rstrip('/')}/api/readings", json=reading, timeout=30)
    if resp.status_code == 422:
        return {"decision": "malformed", "reasons": [resp.json().get("error", "")]}
    resp.raise_for_status()
    return resp.json()


def next_reading(state: dict, hour: float, rated_w: int) -> dict:
    """Take the next seq, simulate the window, sign it, and save the new seq."""
    state["seq"] += 1
    save_meter(state)                         # the counter moves even if sending fails
    return sign(state, simulate_window(state["meterId"], state["seq"], hour, rated_w))


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulated VERDET meter.")
    parser.add_argument("--meter", default="VD-SIM1", help="meter ID (default VD-SIM1)")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="VERDET app URL")
    parser.add_argument("--rated-w", type=int, default=10, help="panel rating in watts (default 10)")
    parser.add_argument("--fast", action="store_true", help="send a window every 5 s instead of 60 s")
    parser.add_argument("--count", type=int, default=0, help="stop after N windows (default: run until Ctrl+C)")
    parser.add_argument("--hour", type=float, help="pretend it's this Lagos hour, e.g. 12 for midday")
    parser.add_argument("--show-key", action="store_true", help="print the public key and exit")
    args = parser.parse_args()

    state = load_meter(args.meter)
    pub = public_key_hex(state)
    if args.show_key:
        print(f"Meter ID:   {args.meter}\nPublic key: {pub}\nRated:      {args.rated_w} W")
        print("Register it at /meters with exactly these values.")
        return 0

    pause = 5 if args.fast else WINDOW_S
    print(f"Simulating {args.meter} ({args.rated_w} W, key {pub[:8]}…) -> {args.url}, one window every {pause} s")
    start = datetime.now(LAGOS)
    sent = 0
    try:
        while args.count == 0 or sent < args.count:
            # In --fast mode each window still represents 1 minute of sun time.
            now = start + timedelta(minutes=sent) if args.fast else datetime.now(LAGOS)
            hour = args.hour if args.hour is not None else now.hour + now.minute / 60
            reading = next_reading(state, hour, args.rated_w)
            try:
                answer = send(args.url, reading)
                reasons = ",".join(answer.get("reasons", [])) or "-"
                print(f"seq {reading['seq']:>5}  {reading['mWh']:>5} mWh  peak {reading['peakMw']:>6} mW"
                      f"  -> {answer['decision']} {reasons}")
            except httpx.HTTPError as exc:
                print(f"seq {reading['seq']:>5}  could not reach {args.url} ({type(exc).__name__}). Is uvicorn running?")
            sent += 1
            if args.count == 0 or sent < args.count:
                time.sleep(pause)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
