"""Attack: send the same genuine reading twice to claim its energy twice.

Expected: DUPLICATE.
Run: python gateway/attacks/duplicate.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gateway import simulator  # noqa: E402
from gateway.attacks._common import (  # noqa: E402
    Send, http_sender, lagos_hour, parse_args, require_meter, show, verdict,
)

EXPECTED = "DUPLICATE"


def attack(send: Send, state: dict, hour: float = 12) -> dict:
    reading = simulator.next_reading(state, hour, rated_w=10)
    show("genuine reading", reading, send(reading))
    answer = send(reading)
    show("SAME reading sent again", reading, answer)
    return answer


def main() -> int:
    args = parse_args("Send one signed reading twice.")
    state = require_meter(args.meter)
    print(f"Attack: duplicate against {args.meter} -> {args.url}")
    return verdict(EXPECTED, attack(http_sender(args.url), state, lagos_hour()))


if __name__ == "__main__":
    sys.exit(main())
