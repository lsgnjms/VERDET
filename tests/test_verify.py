"""Phase 2: canonical string and verification checks 1-3. Run: pytest tests/test_verify.py"""

import hashlib

import pytest
from nacl.signing import SigningKey

from app.models import Meter
from app.verify import (
    BAD_SIGNATURE,
    DUPLICATE,
    REPLAY,
    UNREGISTERED,
    check_sequence,
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
