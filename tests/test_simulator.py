"""Phase 2 step 5: the simulated meter. Run: pytest tests/test_simulator.py"""

import pytest

from app.models import Meter
from app.verify import verify_reading
from gateway import simulator
from gateway.canonical import NUMBER_FIELDS


@pytest.fixture
def sim_state(tmp_path, monkeypatch) -> dict:
    """A fresh simulated meter whose key file lives in a temp folder."""
    monkeypatch.setattr(simulator, "SIM_DIR", tmp_path)
    return simulator.load_meter("VD-TEST")


def registered(state: dict, last_seq: int = -1) -> Meter:
    return Meter(meter_id=state["meterId"], owner_id=1, pubkey_hex=simulator.public_key_hex(state),
                 rated_w=10, lat=6.52, lon=3.38, last_seq=last_seq)


def test_signed_reading_passes_the_apps_checks(sim_state):
    reading = simulator.next_reading(sim_state, hour=12, rated_w=10)
    decision = verify_reading(reading, registered(sim_state))
    assert decision.accepted, decision.reasons


def test_all_numbers_are_integers(sim_state):
    reading = simulator.next_reading(sim_state, hour=12, rated_w=10)
    for field in NUMBER_FIELDS:
        assert type(reading[field]) is int, field


def test_midday_energy_is_plausible_for_a_10w_panel():
    reading = simulator.simulate_window("VD-TEST", seq=0, hour=12.9, rated_w=10)
    # Full sun on 10 W for 1 minute is at most 10000 mW * 60 s / 3600 = 166 mWh.
    assert 100 <= reading["mWh"] <= 175
    assert reading["peakMw"] <= 10 * 1000 * 1.1
    assert reading["tEndMs"] - reading["tStartMs"] == 60_000


def test_night_window_has_no_energy():
    reading = simulator.simulate_window("VD-TEST", seq=0, hour=2, rated_w=10)
    assert reading["mWh"] == 0 and reading["peakMw"] == 0


def test_seq_and_key_persist_across_restarts(sim_state):
    first = simulator.next_reading(sim_state, hour=12, rated_w=10)
    reloaded = simulator.load_meter("VD-TEST")       # like unplugging the meter
    second = simulator.next_reading(reloaded, hour=12, rated_w=10)
    assert (first["seq"], second["seq"]) == (0, 1)
    assert simulator.public_key_hex(reloaded) == simulator.public_key_hex(sim_state)


def test_spike_is_signed_but_fails_physics(sim_state):
    reading = simulator.next_reading(sim_state, hour=12, rated_w=10, spike=True)
    assert reading["peakMw"] > 10 * 1000 * 1.1           # well above the 11 W limit
    decision = verify_reading(reading, registered(sim_state))
    assert decision.reasons == ["PHYSICS_LIMIT"]          # signature was fine; physics caught it
    assert decision.new_last_seq == reading["seq"]


def test_spike_works_even_at_night():
    reading = simulator.simulate_window("VD-TEST", seq=0, hour=2, rated_w=10, spike=True)
    assert reading["peakMw"] > 11_000
