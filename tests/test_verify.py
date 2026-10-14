"""Canonical string and verification checks 1-4. Run: pytest tests/test_verify.py"""

import hashlib

import pytest
from nacl.signing import SigningKey

from app.models import Meter
from app.verify import (
    BAD_SIGNATURE,
    DUPLICATE,
    PHYSICS_LIMIT,
    REPLAY,
    UNREGISTERED,
    check_sequence,
    physics_ok,
    signature_valid,
    verify_reading,
)
from gateway.canonical import canonical_string, reading_hash

# The exact string firmware/spike/spike.ino signs. If this test fails, the
# Python side no longer matches the Mega byte for byte (rule 4).
SPIKE_MSG = "VERDET|v1|VD-0001|1|0|60000|167|10120|18000|560"

METER_KEY = SigningKey(bytes(range(32)))   # fixed test key, not a real meter
OTHER_KEY = SigningKey(bytes(range(1, 33)))


def make_reading(seq: int = 1, key: SigningKey = METER_KEY, **changes) -> dict:
    """A signed reading as it arrives on the wire."""
    reading = {
        "meterId": "VD-0001", "seq": seq, "tStartMs": 0, "tEndMs": 60000,
        "mWh": 167, "peakMw": 10120, "avgMv": 18000, "avgMa": 560,
    }
    reading["sig"] = key.sign(canonical_string(reading).encode()).signature.hex()
    reading.update(changes)   # applied AFTER signing, to simulate tampering
    return reading


def make_signed(seq: int = 1, key: SigningKey = METER_KEY, **values) -> dict:
    """Like make_reading, but the changed values are signed too (an honest-looking
    reading from the meter itself, e.g. a faulty or lying sensor)."""
    reading = {
        "meterId": "VD-0001", "seq": seq, "tStartMs": 0, "tEndMs": 60000,
        "mWh": 167, "peakMw": 10120, "avgMv": 18000, "avgMa": 560,
    }
    reading.update(values)
    reading["sig"] = key.sign(canonical_string(reading).encode()).signature.hex()
    return reading


def make_meter(last_seq: int = -1) -> Meter:
    return Meter(
        meter_id="VD-0001", owner_id=1, pubkey_hex=METER_KEY.verify_key.encode().hex(),
        rated_w=10, lat=6.52, lon=3.38, last_seq=last_seq,
    )


# --- Canonical string -------------------------------------------------------

def test_canonical_string_matches_firmware_byte_for_byte():
    reading = {
        "meterId": "VD-0001", "seq": 1, "tStartMs": 0, "tEndMs": 60000,
        "mWh": 167, "peakMw": 10120, "avgMv": 18000, "avgMa": 560,
    }
    assert canonical_string(reading) == SPIKE_MSG


def test_canonical_string_ignores_extra_fields():
    assert canonical_string(make_reading()) == SPIKE_MSG   # "sig" is not part of it


@pytest.mark.parametrize("bad_value", [167.0, "167", True, -1, None])
def test_canonical_string_rejects_non_integers(bad_value):
    with pytest.raises(ValueError):
        canonical_string(make_reading(mWh=bad_value))


@pytest.mark.parametrize("bad_id", ["VD|0001", "", "x" * 33, "VD 0001", 1])
def test_canonical_string_rejects_bad_meter_id(bad_id):
    with pytest.raises(ValueError):
        canonical_string(make_reading(meterId=bad_id))


def test_canonical_string_rejects_missing_field():
    reading = make_reading()
    del reading["avgMa"]
    with pytest.raises(ValueError, match="avgMa"):
        canonical_string(reading)


def test_reading_hash_is_sha256_of_the_string():
    assert reading_hash(SPIKE_MSG) == hashlib.sha256(SPIKE_MSG.encode()).hexdigest()


# --- Signature --------------------------------------------------------------

def test_signature_valid_for_the_meters_key():
    reading = make_reading()
    assert signature_valid(make_meter().pubkey_hex, SPIKE_MSG, reading["sig"])


def test_signature_invalid_if_one_character_changes():
    reading = make_reading()
    tampered = SPIKE_MSG.replace("|167|", "|168|")
    assert not signature_valid(make_meter().pubkey_hex, tampered, reading["sig"])


def test_signature_invalid_for_another_key():
    reading = make_reading(key=OTHER_KEY)
    assert not signature_valid(make_meter().pubkey_hex, SPIKE_MSG, reading["sig"])


@pytest.mark.parametrize("bad_sig", ["", "zz", "00" * 64, "ab" * 10])
def test_signature_garbage_is_invalid_not_a_crash(bad_sig):
    assert not signature_valid(make_meter().pubkey_hex, SPIKE_MSG, bad_sig)


# --- Sequence ---------------------------------------------------------------

def test_sequence_higher_continues():
    assert check_sequence(seq=6, last_seq=5) is None


def test_sequence_equal_is_duplicate():
    assert check_sequence(seq=5, last_seq=5) == DUPLICATE


def test_sequence_lower_is_replay():
    assert check_sequence(seq=4, last_seq=5) == REPLAY


# --- verify_reading: checks in order ------------------------------------------

def test_good_reading_is_accepted_and_advances_seq():
    d = verify_reading(make_reading(seq=1), make_meter(last_seq=0))
    assert d.accepted and d.reasons == []
    assert d.new_last_seq == 1
    assert d.hash == reading_hash(SPIKE_MSG)


def test_first_reading_seq_zero_is_accepted():
    d = verify_reading(make_reading(seq=0), make_meter(last_seq=-1))
    assert d.accepted and d.new_last_seq == 0


def test_unregistered_meter_is_rejected():
    d = verify_reading(make_reading(), None)
    assert d.reasons == [UNREGISTERED] and d.new_last_seq is None


def test_unregistered_is_checked_before_signature():
    d = verify_reading(make_reading(sig="00"), None)
    assert d.reasons == [UNREGISTERED]


def test_tampered_reading_is_bad_signature_and_does_not_move_seq():
    d = verify_reading(make_reading(seq=1, mWh=99999), make_meter(last_seq=0))
    assert d.reasons == [BAD_SIGNATURE] and d.new_last_seq is None


def test_signature_is_checked_before_sequence():
    # Tampered AND an old seq: the signature check comes first.
    d = verify_reading(make_reading(seq=1, mWh=99999), make_meter(last_seq=5))
    assert d.reasons == [BAD_SIGNATURE]


def test_resent_reading_is_duplicate():
    d = verify_reading(make_reading(seq=5), make_meter(last_seq=5))
    assert d.reasons == [DUPLICATE] and d.new_last_seq == 5


def test_old_reading_is_replay():
    d = verify_reading(make_reading(seq=3), make_meter(last_seq=5))
    assert d.reasons == [REPLAY] and d.new_last_seq == 5


def test_verify_reading_rejects_malformed_input():
    with pytest.raises(ValueError):
        verify_reading(make_reading(mWh=1.5), make_meter())


# --- Physics (Phase 3) --------------------------------------------------------

def physics_reading(**changes) -> dict:
    """Unsigned reading for a 10 W panel; physics_ok only looks at the numbers."""
    reading = {"meterId": "VD-0001", "seq": 1, "tStartMs": 0, "tEndMs": 60000,
               "mWh": 167, "peakMw": 10120, "avgMv": 18000, "avgMa": 560}
    reading.update(changes)
    return reading


def test_physics_normal_reading_passes():
    assert physics_ok(physics_reading(), rated_w=10)


def test_physics_peak_exactly_at_110_percent_passes():
    assert physics_ok(physics_reading(peakMw=11000, mWh=100), rated_w=10)


def test_physics_peak_above_110_percent_fails():
    assert not physics_ok(physics_reading(peakMw=11001, mWh=100), rated_w=10)


def test_physics_energy_exactly_peak_times_window_passes():
    # 12000 mW for 60 s = 200 mWh exactly (on a 20 W panel so the peak is allowed).
    assert physics_ok(physics_reading(peakMw=12000, mWh=200), rated_w=20)


def test_physics_energy_above_peak_times_window_fails():
    assert not physics_ok(physics_reading(peakMw=12000, mWh=201), rated_w=20)


@pytest.mark.parametrize("t_end", [0, -1])
def test_physics_zero_or_negative_window_fails(t_end):
    assert not physics_ok(physics_reading(tStartMs=0, tEndMs=t_end), rated_w=10)


def test_physics_dark_window_passes():
    assert physics_ok(physics_reading(mWh=0, peakMw=0, avgMv=0, avgMa=0), rated_w=10)


def test_spike_reading_is_physics_limit_and_advances_seq():
    # Correctly signed, but 3x the panel's rating: the meter is lying (or broken).
    d = verify_reading(make_signed(seq=6, peakMw=30000, mWh=450), make_meter(last_seq=5))
    assert d.reasons == [PHYSICS_LIMIT]
    assert d.new_last_seq == 6        # signature was valid, so last_seq moves (rule 7)


def test_sequence_is_checked_before_physics():
    d = verify_reading(make_signed(seq=3, peakMw=30000, mWh=450), make_meter(last_seq=5))
    assert d.reasons == [REPLAY]
