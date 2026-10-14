"""Reading verification, in the fixed order of CLAUDE.md rule 7:

    registered -> signature -> sequence -> physics -> AI (Phase 5)

These are pure functions: no database, no network. The /api/readings route
saves the result and moves the meter's last_seq forward.
"""

from dataclasses import dataclass, field

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from app.models import Meter
from gateway.canonical import canonical_string, reading_hash

UNREGISTERED = "UNREGISTERED"
BAD_SIGNATURE = "BAD_SIGNATURE"
REPLAY = "REPLAY"
DUPLICATE = "DUPLICATE"
PHYSICS_LIMIT = "PHYSICS_LIMIT"

MS_PER_HOUR = 3_600_000


@dataclass
class Decision:
    decision: str                     # "accepted" or "rejected"
    reasons: list[str] = field(default_factory=list)
    hash: str = ""                    # sha256 of the canonical string
    new_last_seq: int | None = None   # set when the meter's last_seq should change

    @property
    def accepted(self) -> bool:
        return self.decision == "accepted"


def signature_valid(pubkey_hex: str, canonical: str, sig_hex: str) -> bool:
    """True if sig_hex is a valid Ed25519 signature of canonical under pubkey_hex.
    Malformed hex or wrong lengths count as invalid, never as a crash.
    """
    try:
        VerifyKey(bytes.fromhex(pubkey_hex)).verify(canonical.encode("utf-8"), bytes.fromhex(sig_hex))
        return True
    except (BadSignatureError, ValueError, TypeError):
        return False


def check_sequence(seq: int, last_seq: int) -> str | None:
    """Rule 7: equal -> DUPLICATE, lower -> REPLAY, higher -> None (continue)."""
    if seq == last_seq:
        return DUPLICATE
    if seq < last_seq:
        return REPLAY
    return None


def physics_ok(reading: dict, rated_w: int) -> bool:
    """Electrical sanity, in integer maths (no float rounding):

    - peak power at most 1.1 x the panel rating:    peakMw * 10 <= rated_w * 1000 * 11
    - energy at most peak power for the whole window: mWh * 3_600_000 <= peakMw * window_ms
    """
    window_ms = reading["tEndMs"] - reading["tStartMs"]
    if window_ms <= 0:
        return False
    if reading["peakMw"] * 10 > rated_w * 1000 * 11:
        return False
    return reading["mWh"] * MS_PER_HOUR <= reading["peakMw"] * window_ms


def verify_reading(reading: dict, meter: Meter | None) -> Decision:
    """Run checks 1-4 on one reading. `meter` is None if meterId isn't registered.

    Raises ValueError (from canonical_string) if the reading is malformed;
    the route turns that into an HTTP 422 before any decision is made.
    """
    canonical = canonical_string(reading)
    digest = reading_hash(canonical)

    def reject(code: str, new_last_seq: int | None = None) -> Decision:
        return Decision("rejected", [code], digest, new_last_seq)

    # 1. Registered?
    if meter is None:
        return reject(UNREGISTERED)

    # 2. Signed by this meter's key? (A bad signature never moves last_seq.)
    if not signature_valid(meter.pubkey_hex, canonical, reading.get("sig", "")):
        return reject(BAD_SIGNATURE)

    # 3. Sequence. From here on the reading is genuine, so last_seq advances
    #    whatever the outcome (rule 7). For REPLAY/DUPLICATE it stays the same.
    seq = reading["seq"]
    new_last_seq = max(seq, meter.last_seq)
    problem = check_sequence(seq, meter.last_seq)
    if problem:
        return reject(problem, new_last_seq)

    # 4. Physics: could this panel really have produced this?
    if not physics_ok(reading, meter.rated_w):
        return reject(PHYSICS_LIMIT, new_last_seq)

    # 5. AI (Phase 5) goes here.
    return Decision("accepted", [], digest, new_last_seq)
