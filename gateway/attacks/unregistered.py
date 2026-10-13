"""Attack: a meter nobody registered sends a perfectly signed reading.

Expected: UNREGISTERED.
Run: python gateway/attacks/unregistered.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nacl.signing import SigningKey  # noqa: E402

from gateway import simulator  # noqa: E402
from gateway.attacks._common import Send, http_sender, lagos_hour, parse_args, show, verdict  # noqa: E402

EXPECTED = "UNREGISTERED"


def attack(send: Send, meter_id: str = "VD-ROGUE", hour: float = 12) -> dict:
    # A brand-new key held only in memory: valid signature, unknown meter.
    rogue = {"meterId": meter_id, "seed": SigningKey.generate().encode().hex(), "seq": 0}
    reading = simulator.sign(rogue, simulator.simulate_window(meter_id, 0, hour, rated_w=10))
    answer = send(reading)
    show("rogue meter, valid signature", reading, answer)
    return answer


def main() -> int:
    args = parse_args("Send a signed reading from an unregistered meter.", default_meter="VD-ROGUE")
    print(f"Attack: unregistered meter {args.meter} -> {args.url}")
    return verdict(EXPECTED, attack(http_sender(args.url), args.meter, lagos_hour()))


if __name__ == "__main__":
    sys.exit(main())
