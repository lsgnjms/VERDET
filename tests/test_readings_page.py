"""Phase 3 step 3: the readings page. Run: pytest tests/test_readings_page.py"""

from datetime import datetime, timezone

from sqlmodel import Session

from app.main import format_lagos_time
from app.models import Meter, Reading

TX = "0.0.7179539@1792001515.550792932"
TX_URL = "https://hashscan.io/testnet/transaction/0.0.7179539-1792001515-550792932"


def signup(client, email="ada@example.com"):
    client.post("/signup", data={"email": email, "password": "correct horse"})


def add(engine, *rows) -> None:
    with Session(engine) as session:
        session.add_all(rows)
        session.commit()


def meter(meter_id: str, owner_id: int) -> Meter:
    return Meter(meter_id=meter_id, owner_id=owner_id, pubkey_hex=meter_id.ljust(64, "0"),
                 rated_w=10, lat=6.52, lon=3.38)


def reading(meter_id="VD-0001", seq=1, mwh=150, decision="accepted", reasons="", hcs_tx=TX, mint_tx=TX):
    return Reading(meter_id=meter_id, seq=seq, t_start_ms=0, t_end_ms=60000, mwh=mwh, peak_mw=10000,
                   avg_mv=18000, avg_ma=550, hash="x", decision=decision, reasons=reasons,
                   hcs_tx=hcs_tx, mint_tx=mint_tx)


def table(client) -> str:
    resp = client.get("/readings/table")
    assert resp.status_code == 200
    return resp.text


def test_readings_page_requires_login(client):
    assert client.get("/readings", follow_redirects=False).headers["location"] == "/login"
    assert client.get("/readings/table").status_code == 401


def test_page_loads_the_table_with_htmx(client):
    signup(client)
    html = client.get("/readings").text
    assert 'hx-get="/readings/table?show=all"' in html and "every 5s" in html


def test_no_meters_yet(client):
    signup(client)
    assert "no meters yet" in table(client)


def test_shows_accepted_and_rejected_with_links(client, engine):
    signup(client)                                         # user id 1
    add(engine, meter("VD-0001", 1),
        reading(seq=1, mwh=150),
        reading(seq=2, mwh=300, decision="rejected", reasons="PHYSICS_LIMIT", mint_tx=None))
    html = table(client)
    assert "accepted" in html and "PHYSICS_LIMIT" in html
    assert html.count(TX_URL) == 3                         # 2 HCS proofs + 1 mint
    assert "0.000150" in html and "150 mWh from 1" in html   # only accepted energy counts
    assert "from 1 accepted reading" in html


def test_newest_reading_first(client, engine):
    signup(client)
    add(engine, meter("VD-0001", 1), reading(seq=1), reading(seq=2))
    html = table(client)
    assert html.index(">2<") < html.index(">1<")


def test_pending_states_are_shown(client, engine):
    signup(client)
    add(engine, meter("VD-0001", 1),
        reading(seq=1, hcs_tx=None, mint_tx=None),         # Hedera was down
        reading(seq=2, mwh=0, mint_tx=None))               # dark window: nothing to mint
    html = table(client)
    assert "not anchored" in html
    assert html.count(">pending<") == 1                    # the dark window isn't "pending"
    assert "Rewarded on Hedera so far" in html             # accepted 150 mWh, rewarded 0


def test_no_pending_note_when_everything_is_rewarded(client, engine):
    signup(client)
    add(engine, meter("VD-0001", 1), reading(seq=1))
    assert "Rewarded on Hedera so far" not in table(client)


def test_only_your_own_meters(client, engine):
    signup(client, "ada@example.com")                      # user 1
    client.post("/logout")
    signup(client, "grace@example.com")                    # user 2, logged in
    add(engine, meter("VD-ADA", 1), meter("VD-GRACE", 2),
        reading(meter_id="VD-ADA"), reading(meter_id="VD-GRACE"),
        reading(meter_id="VD-ROGUE", decision="rejected", reasons="UNREGISTERED", mint_tx=None))
    html = table(client)
    assert "VD-GRACE" in html
    assert "VD-ADA" not in html and "VD-ROGUE" not in html


def test_times_are_shown_in_lagos_time():
    utc_noon = datetime(2026, 10, 14, 12, 0, 0)            # naive, as SQLite returns it
    assert format_lagos_time(utc_noon) == "14 Oct 13:00:00"
    assert format_lagos_time(utc_noon.replace(tzinfo=timezone.utc)) == "14 Oct 13:00:00"
