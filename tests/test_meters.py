"""The meter page: register one meter, disconnect it. Run: pytest tests/test_meters.py"""

import html as html_lib
import json

from sqlmodel import Session, select

from app.models import Meter, Reading

PUBKEY = "ab" * 32
OTHER_PUBKEY = "cd" * 32
REGISTRY_TOPIC = "0.0.1001"


def signup(client, email="ada@example.com"):
    client.post("/signup", data={"email": email, "password": "correct horse"})


def add_meter(client, meter_id="VD-0001", pubkey=PUBKEY, rated_w="10"):
    """POST the form and follow the redirect; returns (status of the POST, decoded page)."""
    resp = client.post("/meters", data={"meter_id": meter_id, "pubkey_hex": pubkey, "rated_w": rated_w})
    return resp.history[0].status_code if resp.history else resp.status_code, html_lib.unescape(resp.text)


def all_meters(engine) -> list[Meter]:
    with Session(engine) as session:
        return list(session.exec(select(Meter)))


def registry_messages(fake_hedera) -> list[dict]:
    return [json.loads(m) for topic, m in fake_hedera.messages if topic == REGISTRY_TOPIC]


def test_meters_page_requires_login(client):
    assert client.get("/meters", follow_redirects=False).headers["location"] == "/login"
    resp = client.post("/meters", data={"meter_id": "VD-0001", "pubkey_hex": PUBKEY, "rated_w": "10"},
                       follow_redirects=False)
    assert resp.headers["location"] == "/login"


def test_form_has_no_location_fields(client):
    signup(client)
    page = client.get("/meters").text
    assert 'name="meter_id"' in page and 'name="pubkey_hex"' in page and 'name="rated_w"' in page
    assert 'name="lat"' not in page and 'name="lon"' not in page


def test_add_meter_saves_it_and_records_registry_message(client, engine, fake_hedera):
    signup(client)
    status, page = add_meter(client, pubkey=PUBKEY.upper())
    assert status == 303                                   # redirect back to /meters
    assert "registered and recorded on Hedera" in page
    [meter] = all_meters(engine)
    assert meter.meter_id == "VD-0001" and meter.pubkey_hex == PUBKEY
    assert meter.rated_w == 10 and meter.last_seq == -1
    assert (meter.lat, meter.lon) == (6.52, 3.38)          # sited in Lagos by default
    [msg] = registry_messages(fake_hedera)
    assert msg == {"t": "meter.register", "v": 1, "meterId": "VD-0001", "owner": "0.0.999001",
                   "pubKey": PUBKEY, "ratedW": 10, "lat": 6.52, "lon": 3.38}


def test_form_disappears_once_a_meter_is_added(client):
    signup(client)
    add_meter(client)
    page = client.get("/meters").text
    assert 'name="pubkey_hex"' not in page                 # no registration form
    assert "VD-0001" in page and "Disconnect" in page


def test_only_one_meter_per_account(client, engine, fake_hedera):
    signup(client)
    add_meter(client)
    status, page = add_meter(client, meter_id="VD-0002", pubkey=OTHER_PUBKEY)
    assert status == 400 and "Disconnect it first" in page
    assert len(all_meters(engine)) == 1 and len(registry_messages(fake_hedera)) == 1


def test_duplicate_meter_id_rejected_without_hedera(client, engine, fake_hedera):
    signup(client, "ada@example.com")
    add_meter(client)
    client.post("/logout")
    signup(client, "grace@example.com")
    status, page = add_meter(client, pubkey=OTHER_PUBKEY)
    assert status == 400 and "already registered" in page
    assert len(registry_messages(fake_hedera)) == 1 and len(all_meters(engine)) == 1


def test_duplicate_public_key_rejected_without_hedera(client, engine, fake_hedera):
    signup(client, "ada@example.com")
    add_meter(client)
    client.post("/logout")
    signup(client, "grace@example.com")
    status, page = add_meter(client, meter_id="VD-0002")
    assert status == 400 and "One device, one meter" in page
    assert len(registry_messages(fake_hedera)) == 1 and len(all_meters(engine)) == 1


def test_bad_form_values_rejected_without_hedera(client, engine, fake_hedera):
    signup(client)
    for bad in [{"meter_id": "VD|0001"}, {"pubkey": "ab" * 31}, {"pubkey": "zz" * 32},
                {"rated_w": "ten"}, {"rated_w": "0"}, {"rated_w": "10001"}]:
        status, _ = add_meter(client, **bad)
        assert status == 400, bad
    assert fake_hedera.messages == [] and all_meters(engine) == []


def test_bad_form_keeps_what_the_user_typed(client):
    signup(client)
    _, page = add_meter(client, meter_id="VD-0042", rated_w="ten")
    assert 'value="VD-0042"' in page


def test_hedera_failure_saves_no_meter(client, engine, fake_hedera):
    signup(client)
    fake_hedera.fail = True
    status, page = add_meter(client)
    assert status == 503 and "try again" in page
    assert all_meters(engine) == []


def test_users_only_see_their_own_meter(client):
    signup(client, "ada@example.com")
    add_meter(client, meter_id="VD-ADA")
    client.post("/logout")
    signup(client, "grace@example.com")
    add_meter(client, meter_id="VD-GRACE", pubkey=OTHER_PUBKEY)
    page = client.get("/meters").text
    assert "VD-GRACE" in page and "VD-ADA" not in page


# --- Disconnect ------------------------------------------------------------------

def test_disconnect_removes_meter_and_records_it(client, engine, fake_hedera):
    signup(client)
    add_meter(client)
    page = html_lib.unescape(client.post("/meters/disconnect").text)
    assert "disconnected" in page and 'name="pubkey_hex"' in page     # the form is back
    assert all_meters(engine) == []
    assert registry_messages(fake_hedera)[-1] == {"t": "meter.disconnect", "v": 1, "meterId": "VD-0001",
                                                  "owner": "0.0.999001"}


def test_disconnect_needs_hedera(client, engine, fake_hedera):
    signup(client)
    add_meter(client)
    fake_hedera.fail = True
    page = html_lib.unescape(client.post("/meters/disconnect").text)
    assert "Couldn't record the disconnect" in page
    assert len(all_meters(engine)) == 1


def test_reregistering_continues_from_the_last_genuine_seq(client, engine):
    """Old signed readings must not become replayable after disconnect + re-register."""
    signup(client)
    add_meter(client)
    with Session(engine) as session:
        session.add_all([
            Reading(meter_id="VD-0001", seq=7, t_start_ms=0, t_end_ms=60000, mwh=150, peak_mw=9000,
                    avg_mv=18000, avg_ma=500, hash="x", decision="accepted"),
            Reading(meter_id="VD-0001", seq=99, t_start_ms=0, t_end_ms=60000, mwh=1, peak_mw=1,
                    avg_mv=1, avg_ma=1, hash="x", decision="rejected", reasons="BAD_SIGNATURE"),  # forged: ignored
        ])
        session.commit()
    client.post("/meters/disconnect")
    add_meter(client)
    [meter] = all_meters(engine)
    assert meter.last_seq == 7
