"""The canonical signed string: the ONE Python place it is built (CLAUDE.md rule 4).

    VERDET|v1|{meterId}|{seq}|{tStartMs}|{tEndMs}|{mWh}|{peakMw}|{avgMv}|{avgMa}

The meter signs the UTF-8 bytes of this string with Ed25519. It must match
firmware/meter/meter.ino byte for byte. If you change it, change both, bump
VERSION, and update tests/test_verify.py.
"""

import hashlib
import re

VERSION = "v1"

# Order matters: this is the order the values appear in the signed string.
NUMBER_FIELDS = ("seq", "tStartMs", "tEndMs", "mWh", "peakMw", "avgMv", "avgMa")
FIELDS = ("meterId",) + NUMBER_FIELDS

# Letters, digits and dashes only, so a meterId can never contain the "|" separator.
METER_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,32}$")


def canonical_string(reading: dict) -> str:
    """Build the signed string from a reading dict (camelCase keys, as on the wire).

    Raises ValueError if a field is missing, a number isn't a plain non-negative
    integer (rule 5: no floats in signed data), or the meterId is malformed.
    """
    missing = [f for f in FIELDS if f not in reading]
    if missing:
        raise ValueError(f"Missing fields: {', '.join(missing)}")

    meter_id = reading["meterId"]
    if not isinstance(meter_id, str) or not METER_ID_PATTERN.match(meter_id):
        raise ValueError("meterId must be 1-32 letters, digits or dashes.")

    for field in NUMBER_FIELDS:
        value = reading[field]
        # bool is a subclass of int in Python, so rule it out explicitly.
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{field} must be a non-negative integer.")

    values = [meter_id] + [str(reading[f]) for f in NUMBER_FIELDS]
    return "|".join(["VERDET", VERSION] + values)


def reading_hash(canonical: str) -> str:
    """SHA-256 of the canonical string, as hex. Goes into the HCS message."""
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
