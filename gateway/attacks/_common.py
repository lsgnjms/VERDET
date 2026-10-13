"""Shared bits for the attack scripts: arguments, the target meter, and the verdict."""

import argparse
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # so "gateway" imports

from gateway import simulator  # noqa: E402

Send = Callable[[dict], dict]   # takes a reading, returns the app's JSON answer


def parse_args(description: str, default_meter: str = "VD-SIM1") -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--meter", default=default_meter, help=f"meter ID (default {default_meter})")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="VERDET app URL")
    return parser.parse_args()


def require_meter(meter_id: str) -> dict:
    """Load the simulated meter's key. It must already exist (and be registered)."""
    if not simulator._state_path(meter_id).exists():
        sys.exit(
            f"No simulated meter {meter_id} yet. Run `python gateway/simulator.py --show-key`, "
            "register it on /meters, then try again."
        )
    return simulator.load_meter(meter_id)


def http_sender(url: str) -> Send:
    return lambda reading: simulator.send(url, reading)


def lagos_hour() -> float:
    now = datetime.now(simulator.LAGOS)
    return now.hour + now.minute / 60


def show(label: str, reading: dict, answer: dict) -> None:
    reasons = ",".join(answer.get("reasons", [])) or "-"
    print(f"  {label:<34} seq {reading['seq']:>5}  mWh {reading['mWh']:>6}  -> {answer['decision']} {reasons}")


def verdict(expected: str, answer: dict) -> int:
    """Print PASS/FAIL. Exit code 0 only if the app rejected with exactly `expected`."""
    got = answer.get("reasons", [])
    if answer.get("decision") == "rejected" and got == [expected]:
        print(f"\nPASS: the app rejected the attack with {expected}.")
        return 0
    print(f"\nFAIL: expected rejected [{expected}], got {answer.get('decision')} {got}.")
    return 1
