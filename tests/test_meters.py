"""Phase 2 step 3: the Add-meter page. Run: pytest tests/test_meters.py"""

import json

from sqlmodel import Session, select

from app.models import Meter

PUBKEY = "ab" * 32
OTHER_PUBKEY = "cd" * 32


def signup(client, email="ada@example.com"):
    client.post("/signup", data={"email": email, "password": "correct horse"})


def add_meter(client, meter_id="VD-0001", pubkey=PUBKEY, rated_w="10", lat="6.52", lon="3.38"):
    return client.post(
        "/meters",
        data={"meter_id": meter_id, "pubkey_hex": pubkey, "rated_w": rated_w, "lat": lat, "lon": lon},
    )


def all_meters(engine) -> list[Meter]:
    with Session(engine) as session:
        return list(session.exec(select(Meter)))


def test_meters_page_requires_login(client):
    assert client.get("/meters", follow_redirects=False).headers["location"] == "/login"
    resp = client.post(
        "/meters",
        data={"meter_id": "VD-0001", "pubkey_hex": PUBKEY, "rated_w": "10", "lat": "0", "lon": "0"},
        follow_redirects=False,
    )
    assert resp.headers["location"] == "/login"


def test_add_meter_saves_it_and_records_registry_message(client, engine, fake_hedera):
    signup(client)
    resp = add_meter(client, pubkey=PUBKEY.upper())
    assert resp.status_code == 200
    assert "registered and recorded on Hedera" in resp.text
    assert "hashscan.io/testnet/transaction/" in resp.text

    [meter] = all_meters(engine)
    assert meter.meter_id == "VD-0001"
    assert meter.pubkey_hex == PUBKEY          # stored lowercase
    assert meter.rated_w == 10 and meter.last_seq == -1

    [(topic, raw)] = fake_hedera.messages
    assert topic == "0.0.1001"                 # the registry topic
    assert len(raw.encode()) < 1024            # rule 6
    msg = json.loads(raw)
    assert msg == {
        "t": "meter.register", "v": 1, "meterId": "VD-0001", "owner": "0.0.999001",
        "pubKey": PUBKEY, "ratedW": 10, "lat": 6.52, "lon": 3.38,
    }


def test_duplicate_meter_id_rejected_without_hedera(client, engine, fake_hedera):
    signup(client)
    add_meter(client)
    resp = add_meter(client, pubkey=OTHER_PUBKEY)
    assert resp.status_code == 400
    assert "already registered" in resp.text
    assert len(fake_hedera.messages) == 1
    assert len(all_meters(engine)) == 1


def test_duplicate_public_key_rejected_without_hedera(client, engine, fake_hedera):
    signup(client)
    add_meter(client)
    resp = add_meter(client, meter_id="VD-0002")
    assert resp.status_code == 400
    assert "One device, one meter" in resp.text
    assert len(fake_hedera.messages) == 1
    assert len(all_meters(engine)) == 1


def test_bad_form_values_rejected_without_hedera(client, engine, fake_hedera):
    signup(client)
    bad_inputs = [
        {"meter_id": "VD|0001"},
        {"pubkey": "ab" * 31},                 # too short
        {"pubkey": "zz" * 32},                 # not hex
        {"rated_w": "ten"},
        {"rated_w": "0"},
        {"rated_w": "10001"},
        {"lat": "91"},
        {"lon": "abc"},
    ]
    for bad in bad_inputs:
        resp = add_meter(client, **bad)
        assert resp.status_code == 400, bad
    assert fake_hedera.messages == []
    assert all_meters(engine) == []


def test_bad_form_keeps_what_the_user_typed(client):
    signup(client)
    resp = add_meter(client, meter_id="VD-0042", rated_w="ten")
    assert 'value="VD-0042"' in resp.text


def test_hedera_failure_saves_no_meter(client, engine, fake_hedera):
    signup(client)
    fake_hedera.fail = True
    resp = add_meter(client)
    assert resp.status_code == 503
    assert "try again" in resp.text
    assert all_meters(engine) == []


def test_users_only_see_their_own_meters(client):
    signup(client, "ada@example.com")
    add_meter(client, meter_id="VD-ADA")
    client.post("/logout")
    signup(client, "grace@example.com")
    add_meter(client, meter_id="VD-GRACE", pubkey=OTHER_PUBKEY)
    html = client.get("/meters").text
    assert "VD-GRACE" in html
    assert "VD-ADA" not in html
