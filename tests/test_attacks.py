"""Phase 2 step 6: each attack script gets exactly its rejection code.
Run: pytest tests/test_attacks.py
"""

import pytest
from sqlmodel import Session, select

from app.models import Meter, Reading, User
from gateway import simulator
from gateway.attacks import duplicate, replay, tamper, unregistered


@pytest.fixture
def sim_meter(engine, tmp_path, monkeypatch) -> dict:
    """A simulated meter (key in a temp folder) registered in the test database."""
    monkeypatch.setattr(simulator, "SIM_DIR", tmp_path)
    state = simulator.load_meter("VD-SIM1")
    with Session(engine) as session:
        user = User(email="ada@example.com", password_hash="x", account_id="0.0.999001", encrypted_key="x")
        session.add(user)
        session.commit()
        session.add(Meter(meter_id="VD-SIM1", owner_id=user.id, pubkey_hex=simulator.public_key_hex(state),
                          rated_w=10, lat=6.52, lon=3.38))
        session.commit()
    return state


@pytest.fixture
def send(client):
    def _send(reading: dict) -> dict:
        resp = client.post("/api/readings", json=reading)
        assert resp.status_code == 200, resp.text
        return resp.json()
    return _send


def meter_last_seq(engine) -> int:
    with Session(engine) as session:
        return session.exec(select(Meter)).one().last_seq


def test_unregistered_attack(send, sim_meter):
    assert unregistered.attack(send)["reasons"] == ["UNREGISTERED"]


def test_tamper_attack(send, sim_meter, engine):
    assert tamper.attack(send, sim_meter)["reasons"] == ["BAD_SIGNATURE"]
    assert meter_last_seq(engine) == -1          # the forgery didn't move the counter


def test_replay_attack(send, sim_meter):
    assert replay.attack(send, sim_meter)["reasons"] == ["REPLAY"]


def test_duplicate_attack(send, sim_meter):
    assert duplicate.attack(send, sim_meter)["reasons"] == ["DUPLICATE"]


def test_meter_keeps_working_after_all_attacks(send, sim_meter, engine):
    unregistered.attack(send)
    tamper.attack(send, sim_meter)
    replay.attack(send, sim_meter)
    duplicate.attack(send, sim_meter)
    answer = send(simulator.next_reading(sim_meter, hour=12, rated_w=10))
    assert answer["decision"] == "accepted"
    with Session(engine) as session:
        codes = [r.reasons for r in session.exec(select(Reading).where(Reading.decision == "rejected"))]
    assert sorted(codes) == ["BAD_SIGNATURE", "DUPLICATE", "REPLAY", "UNREGISTERED"]
