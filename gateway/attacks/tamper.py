"""Attack: a dishonest gateway inflates a genuine reading after the meter signed it.

Expected: BAD_SIGNATURE (and the meter's last_seq does not move).
Run: python gateway/attacks/tamper.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gateway import simulator  # noqa: E402
from gateway.attacks._common import (  # noqa: E402
    Send, http_sender, lagos_hour, parse_args, require_meter, show, verdict,
)

EXPECTED = "BAD_SIGNATURE"


def attack(send: Send, state: dict, hour: float = 12) -> dict:
    # The meter signs its next window... (seq is not saved: the gateway never
    # gets a valid reading through, so the real meter can still use this seq)
    seq = state["seq"] + 1
    signed = simulator.sign(state, simulator.simulate_window(state["meterId"], seq, hour, rated_w=10))
    # ...and the gateway multiplies the energy by 10 without re-signing (it has no key).
    forged = dict(signed, mWh=signed["mWh"] * 10 + 1000)
    answer = send(forged)
    show("energy inflated after signing", forged, answer)
    return answer


def main() -> int:
    args = parse_args("Inflate a signed reading's energy and send it.")
    state = require_meter(args.meter)
    print(f"Attack: tampered reading from {args.meter} -> {args.url}")
    return verdict(EXPECTED, attack(http_sender(args.url), state, lagos_hour()))


if __name__ == "__main__":
    sys.exit(main())
