"""Phase 2 step 4: POST /api/readings. Run: pytest tests/test_readings_api.py"""

import pytest
from sqlmodel import Session, select

from app.models import Meter, Reading, User
from tests.test_verify import METER_KEY, OTHER_KEY, make_reading


@pytest.fixture
def meter(engine) -> Meter:
    """A registered meter VD-0001 (key METER_KEY) owned by a test user."""
    with Session(engine) as session:
        user = User(email="ada@example.com", password_hash="x", account_id="0.0.999001", encrypted_key="x")
        session.add(user)
        session.commit()
        meter = Meter(
            meter_id="VD-0001", owner_id=user.id, pubkey_hex=METER_KEY.verify_key.encode().hex(),
            rated_w=10, lat=6.52, lon=3.38,
        )
        session.add(meter)
        session.commit()
        session.refresh(meter)
        return meter


def post(client, reading: dict) -> dict:
    resp = client.post("/api/readings", json=reading)
    assert resp.status_code == 200, resp.text
    return resp.json()


def last_seq(engine) -> int:
    with Session(engine) as session:
        return session.exec(select(Meter).where(Meter.meter_id == "VD-0001")).one().last_seq


def stored(engine) -> list[Reading]:
    with Session(engine) as session:
        return list(session.exec(select(Reading).order_by(Reading.id)))


def test_good_reading_is_accepted_stored_and_moves_seq(client, engine, meter):
    body = post(client, make_reading(seq=0))
    assert body["decision"] == "accepted" and body["reasons"] == []
    assert last_seq(engine) == 0
    [row] = stored(engine)
    assert (row.meter_id, row.seq, row.mwh, row.decision) == ("VD-0001", 0, 167, "accepted")
    assert row.hash == body["hash"] and len(row.hash) == 64


def test_stream_of_readings_all_accepted(client, engine, meter):
    for seq in range(1, 6):
        assert post(client, make_reading(seq=seq))["decision"] == "accepted"
    assert last_seq(engine) == 5
    assert len(stored(engine)) == 5


def test_unregistered_meter_is_rejected_and_stored(client, engine, meter):
    body = post(client, make_reading(meterId="VD-9999"))
    assert body["reasons"] == ["UNREGISTERED"]
    [row] = stored(engine)
    assert row.decision == "rejected" and row.reasons == "UNREGISTERED"
    assert last_seq(engine) == -1


def test_tampered_reading_is_bad_signature_and_seq_unchanged(client, engine, meter):
    post(client, make_reading(seq=1))
    body = post(client, make_reading(seq=2, mWh=99999))
    assert body["reasons"] == ["BAD_SIGNATURE"]
    assert last_seq(engine) == 1                   # a forgery never moves last_seq


def test_reading_signed_by_another_key_is_bad_signature(client, engine, meter):
    body = post(client, make_reading(seq=1, key=OTHER_KEY))
    assert body["reasons"] == ["BAD_SIGNATURE"]
    assert last_seq(engine) == -1


def test_resent_reading_is_duplicate(client, engine, meter):
    reading = make_reading(seq=3)
    assert post(client, reading)["decision"] == "accepted"
    assert post(client, reading)["reasons"] == ["DUPLICATE"]
    assert last_seq(engine) == 3
    assert [r.decision for r in stored(engine)] == ["accepted", "rejected"]


def test_old_reading_is_replay(client, engine, meter):
    old = make_reading(seq=1)
    post(client, old)
    post(client, make_reading(seq=2))
    assert post(client, old)["reasons"] == ["REPLAY"]
    assert last_seq(engine) == 2


def test_race_backstop_turns_second_accept_into_duplicate(client, engine, meter):
    """Simulate two copies of seq 5 arriving at once: the first is already
    stored as accepted, but the meter's last_seq hasn't been updated yet."""
    with Session(engine) as session:
        m = session.exec(select(Meter)).one()
        m.last_seq = 4
        session.add(m)
        session.add(Reading(
            meter_id="VD-0001", seq=5, t_start_ms=0, t_end_ms=60000, mwh=167, peak_mw=10120,
            avg_mv=18000, avg_ma=560, hash="x", decision="accepted",
        ))
        session.commit()

    body = post(client, make_reading(seq=5))       # passes the seq check (5 > 4)...
    assert body["reasons"] == ["DUPLICATE"]        # ...but the database blocks it
    assert last_seq(engine) == 5
    rows = stored(engine)
    assert [(r.seq, r.decision) for r in rows] == [(5, "accepted"), (5, "rejected")]


@pytest.mark.parametrize("change", [{"mWh": 1.5}, {"seq": "7"}, {"meterId": "VD|1"}, {"avgMa": None}])
def test_malformed_reading_is_422_and_not_stored(client, engine, meter, change):
    reading = make_reading(seq=1)
    reading.update(change)
    resp = client.post("/api/readings", json=reading)
    assert resp.status_code == 422
    assert "error" in resp.json()
    assert stored(engine) == []
    assert last_seq(engine) == -1


def test_missing_field_is_422(client, engine, meter):
    reading = make_reading(seq=1)
    del reading["tEndMs"]
    resp = client.post("/api/readings", json=reading)
    assert resp.status_code == 422
    assert stored(engine) == []


def test_readings_api_needs_no_login(client, meter):
    # No signup in this test: the signature is the credential.
    assert post(client, make_reading(seq=1))["decision"] == "accepted"
