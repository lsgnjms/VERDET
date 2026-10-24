"""Reading detail, live events, onboarding checklist, price band, price history, footer.
Run: pytest tests/test_features.py"""

import html as html_lib
import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlmodel import Session, select

import app.models as models
from app.main import app
from app.models import Meter, Offer, Reading, User
from gateway.canonical import canonical_string, reading_hash
from tests.test_verify import METER_KEY, make_reading

TX = "0.0.7179539@1792001515.550792932"


def signup(client, email="ada@example.com"):
    client.post("/signup", data={"email": email, "password": "correct horse"})


def add(engine, *rows):
    with Session(engine) as session:
        session.add_all(rows)
        session.commit()


def real_meter(owner_id=1) -> Meter:
    """VD-0001 with the test signing key, so signatures really verify."""
    return Meter(meter_id="VD-0001", owner_id=owner_id, pubkey_hex=METER_KEY.verify_key.encode().hex(),
                 rated_w=10, lat=6.52, lon=3.38)


def page(resp) -> str:
    return html_lib.unescape(resp.text)


# --- Signature is stored -----------------------------------------------------------

def test_received_reading_keeps_its_signature(client, engine):
    signup(client)
    add(engine, real_meter())
    sent = make_reading(seq=1)
    client.post("/api/readings", json=sent)
    with Session(engine) as session:
        assert session.exec(select(Reading)).one().sig == sent["sig"]


def test_missing_columns_are_added_to_old_databases(monkeypatch):
    old = create_engine("sqlite://")
    with old.begin() as conn:
        conn.execute(text("CREATE TABLE readings (id INTEGER PRIMARY KEY, meter_id VARCHAR)"))
    monkeypatch.setattr(models, "engine", old)
    models.add_missing_columns()
    assert "sig" in {c["name"] for c in inspect(old).get_columns("readings")}


# --- Reading detail ------------------------------------------------------------------------

def stored_reading(client, engine, **changes) -> int:
    """Send a signed reading through the real API and return its database id."""
    signup(client)
    add(engine, real_meter())
    client.post("/api/readings", json=make_reading(seq=1, **changes))
    with Session(engine) as session:
        return session.exec(select(Reading)).one().id


def test_detail_shows_signed_string_and_checks(client, engine):
    rid = stored_reading(client, engine)
    html = page(client.get(f"/readings/{rid}"))
    assert "VERDET|v1|VD-0001|1|0|60000|167|10120|18000|560" in html
    assert "matches the string ✓" in html                       # stored hash = hash of the rebuilt string
    assert "signature is valid for this meter's key" in html
    assert "Verify in your browser" in html and "nacl-fast.min.js" in html


def test_detail_of_a_forged_reading_says_so(client, engine):
    rid = stored_reading(client, engine, mWh=99999)              # changed after signing
    html = page(client.get(f"/readings/{rid}"))
    assert "BAD_SIGNATURE" in html
    assert "does NOT match this meter's key" in html


def test_detail_is_private_to_the_meter_owner(client, engine):
    rid = stored_reading(client, engine)
    other = TestClient(app)
    signup(other, "grace@example.com")
    resp = other.get(f"/readings/{rid}", follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"] == "/readings"
    assert other.get(f"/readings/{rid}/hcs").status_code == 404


def test_hcs_partial_shows_the_message_from_hedera(client, engine, fake_hedera):
    rid = stored_reading(client, engine)
    with Session(engine) as session:
        r = session.get(Reading, rid)
        recorded = {"t": "reading", "v": 1, "meterId": "VD-0001", "seq": 1, "hash": r.hash, "decision": "accepted"}
    fake_hedera.hcs_message = {"sequence": 42, "consensus": "1792001516.000000001", "text": json.dumps(recorded)}
    html = page(client.get(f"/readings/{rid}/hcs"))
    assert "#42" in html and "Hash on Hedera matches this reading ✓" in html


def test_hcs_partial_when_mirror_node_is_behind(client, engine, fake_hedera):
    rid = stored_reading(client, engine)
    fake_hedera.hcs_message = None
    assert "Not on the mirror node yet" in page(client.get(f"/readings/{rid}/hcs"))


def test_readings_table_links_to_details(client, engine):
    rid = stored_reading(client, engine)
    assert f'href="/readings/{rid}"' in client.get("/readings/table").text


# --- Live events -----------------------------------------------------------------------------

def test_events_start_from_the_current_reading(client, engine):
    rid = stored_reading(client, engine)
    first = client.get("/api/me/events").json()
    assert first == {"cursor": rid, "events": []}                # old readings don't pop up


def test_events_report_new_settled_decisions(client, engine):
    signup(client)
    add(engine, real_meter())
    cursor = client.get("/api/me/events").json()["cursor"]
    client.post("/api/readings", json=make_reading(seq=1))                       # accepted, minted
    client.post("/api/readings", json=make_reading(seq=1))                       # DUPLICATE
    data = client.get(f"/api/me/events?after={cursor}").json()
    assert [(e["decision"], e["reasons"], e["minted"]) for e in data["events"]] == [
        ("accepted", "", True), ("rejected", "DUPLICATE", False)]
    assert data["events"][0]["vkwh"] == "0.000167"
    assert client.get(f"/api/me/events?after={data['cursor']}").json()["events"] == []


def test_events_wait_until_hedera_has_finished(client, engine):
    signup(client)
    add(engine, real_meter(), Reading(meter_id="VD-0001", seq=5, t_start_ms=0, t_end_ms=60000, mwh=150,
                                      peak_mw=9000, avg_mv=18000, avg_ma=500, hash="x", decision="accepted"))
    data = client.get("/api/me/events?after=0").json()
    assert data == {"cursor": 0, "events": []}                  # no hcs_tx yet and only seconds old


def test_old_backlog_is_skipped_but_new_decisions_still_pop_up(client, engine):
    """After time away, hundreds of old readings must not flood the screen."""
    signup(client)
    old = datetime.now(timezone.utc) - timedelta(minutes=10)
    backlog = [Reading(meter_id="VD-0001", seq=s, t_start_ms=0, t_end_ms=60000, mwh=150, peak_mw=9000,
                       avg_mv=18000, avg_ma=500, hash="x", decision="accepted", hcs_tx=TX, mint_tx=TX,
                       created_at=old) for s in range(1, 51)]
    add(engine, real_meter(), *backlog)
    data = client.get("/api/me/events?after=0").json()
    assert data["events"] == [] and data["cursor"] == 50         # skipped silently, cursor caught up
    client.post("/api/readings", json=make_reading(seq=51))      # a fresh one
    fresh = client.get(f"/api/me/events?after={data['cursor']}").json()["events"]
    assert [e["seq"] for e in fresh] == [51]


def test_events_need_login(client):
    assert client.get("/api/me/events").status_code == 401


# --- Onboarding checklist ------------------------------------------------------------------------

def test_checklist_for_a_new_user(client):
    signup(client)
    html = page(client.get("/dashboard"))
    assert "Get started" in html and "1 of 5 done" in html
    assert 'href="/meters" class="mt-2' in html                # "Do it now" points at the next step


def test_checklist_progress_and_disappears_when_done(client, engine):
    signup(client)
    add(engine, real_meter(), User(email="b@x.y", password_hash="x", account_id="0.0.2", encrypted_key="x"))
    client.post("/api/readings", json=make_reading(seq=1))
    assert "4 of 5 done" in page(client.get("/dashboard"))     # account, meter, reading, VKWH
    add(engine, Offer(seller_id=1, buyer_id=2, amount_mwh=100, price_tinybar=1000, status="filled", tx_id=TX))
    assert "Get started" not in page(client.get("/dashboard"))


# --- Price band and history ----------------------------------------------------------------------------

def list_offer(client, fake_hedera, amount, per_kwh) -> str:
    fake_hedera.balances = {"hbar_tinybar": 10**9, "vkwh_mwh": 1_000_000}
    return page(client.post("/offers", data={"amount_vkwh": amount, "price_per_kwh": per_kwh}))


def offers_in(engine) -> list[Offer]:
    with Session(engine) as session:
        return list(session.exec(select(Offer)))


def test_price_band_refuses_too_expensive(client, engine, fake_hedera):
    signup(client)
    html = list_offer(client, fake_hedera, "0.001", "1000")
    assert "Price must be between 1 and 50 HBAR per kWh" in html
    assert offers_in(engine) == []


def test_price_band_refuses_too_cheap(client, fake_hedera):
    signup(client)
    assert "Price must be between" in list_offer(client, fake_hedera, "1", "0.5")


def test_price_band_accepts_the_edges(client, engine, fake_hedera):
    signup(client)
    list_offer(client, fake_hedera, "0.1", "1")
    list_offer(client, fake_hedera, "0.1", "50")
    assert [o.price_tinybar for o in offers_in(engine)] == [10_000_000, 500_000_000]   # 0.1 and 5 HBAR


def test_total_is_computed_from_price_per_kwh(client, engine, fake_hedera):
    """The user's example: 0.0004 VKWH at 10 HBAR/kWh = 0.004 HBAR, no guessing."""
    signup(client)
    html = list_offer(client, fake_hedera, "0.0004", "10")
    assert "You'll receive 0.004 HBAR" in html
    [offer] = offers_in(engine)
    assert (offer.amount_mwh, offer.price_tinybar) == (400, 400_000)


def test_minimum_offer_size(client, engine, fake_hedera):
    signup(client)
    assert "The smallest offer is 0.000100 VKWH" in list_offer(client, fake_hedera, "0.00005", "10")
    assert offers_in(engine) == []


def test_sell_form_is_prefilled_with_the_reference_price(client):
    signup(client)
    html = page(client.get("/market"))
    assert 'name="price_per_kwh"' in html and 'value="10"' in html
    assert 'name="price_hbar"' not in html


def test_suggested_price_follows_the_last_trade_inside_the_band(client, engine):
    signup(client)
    add(engine, User(email="b@x.y", password_hash="x", account_id="0.0.2", encrypted_key="x"),
        Offer(seller_id=2, buyer_id=1, amount_mwh=100_000, price_tinybar=12 * 10**7, status="filled", tx_id=TX))
    assert 'value="12.00"' in page(client.get("/market"))                 # 1.2 HBAR for 0.1 VKWH


def test_market_shows_band_and_price_history(client, engine):
    signup(client)
    add(engine, User(email="b@x.y", password_hash="x", account_id="0.0.2", encrypted_key="x"),
        Offer(seller_id=2, buyer_id=1, amount_mwh=100_000, price_tinybar=10 * 10**8, status="filled", tx_id=TX))
    html = page(client.get("/market"))
    assert "allowed range" in html
    assert '"p": 100.0' in html and 'id="price-chart"' in html     # 10 HBAR for 0.1 VKWH = 100 HBAR/kWh


# --- Branding ------------------------------------------------------------------------------------------

def test_no_event_branding_in_the_product(client):
    for url in ["/", "/impact", "/login", "/signup"]:
        html = client.get(url).text
        assert "Hackathon" not in html and "IEEE" not in html and "ClimateChain" not in html
