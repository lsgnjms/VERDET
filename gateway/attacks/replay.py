"""Attack: resend an OLD genuine reading after newer ones were accepted.

Expected: REPLAY.
Run: python gateway/attacks/replay.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gateway import simulator  # noqa: E402
from gateway.attacks._common import (  # noqa: E402
    Send, http_sender, lagos_hour, parse_args, require_meter, show, verdict,
)

EXPECTED = "REPLAY"


def attack(send: Send, state: dict, hour: float = 12) -> dict:
    old = simulator.next_reading(state, hour, rated_w=10)
    show("genuine reading (will be replayed)", old, send(old))
    newer = simulator.next_reading(state, hour, rated_w=10)
    show("genuine newer reading", newer, send(newer))
    answer = send(old)
    show("REPLAY of the old reading", old, answer)
    return answer


def main() -> int:
    args = parse_args("Resend an old signed reading.")
    state = require_meter(args.meter)
    print(f"Attack: replay against {args.meter} -> {args.url}")
    return verdict(EXPECTED, attack(http_sender(args.url), state, lagos_hour()))


if __name__ == "__main__":
    sys.exit(main())
