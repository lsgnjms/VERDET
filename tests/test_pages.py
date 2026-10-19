"""Phase 7 pages: landing, dashboard, live power feed, impact, account menu, market layout.
Run: pytest tests/test_pages.py"""

import re

from sqlmodel import Session

from app.main import format_energy
from app.models import Meter, Offer, Reading, User

TX = "0.0.7179539@1792001515.550792932"


def signup(client, email="ada@example.com"):
    client.post("/signup", data={"email": email, "password": "correct horse"})


def add(engine, *rows):
    with Session(engine) as session:
        session.add_all(rows)
        session.commit()


def meter(owner_id=1, meter_id="VD-0001"):
    return Meter(meter_id=meter_id, owner_id=owner_id, pubkey_hex=meter_id.ljust(64, "0"), rated_w=10, lat=6.52, lon=3.38)


def reading(seq, mwh=150, decision="accepted", reasons="", meter_id="VD-0001"):
    return Reading(meter_id=meter_id, seq=seq, t_start_ms=0, t_end_ms=60000, mwh=mwh, peak_mw=10000,
                   avg_mv=18000, avg_ma=550, hash="x", decision=decision, reasons=reasons, hcs_tx=TX,
                   mint_tx=TX if decision == "accepted" else None)


def test_format_energy_picks_a_readable_unit():
    assert format_energy(167) == "167 mWh"
    assert format_energy(1_240) == "1.24 Wh"
    assert format_energy(1_234_567) == "1.235 kWh"


# --- Landing ------------------------------------------------------------------

def test_landing_is_public_and_shows_live_numbers(client, engine):
    add(engine, User(email="x@y.z", password_hash="x", account_id="0.0.1", encrypted_key="x"), meter(),
        reading(1, mwh=1_500), reading(2, decision="rejected", reasons="REPLAY"))
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 200
    assert "Verified solar energy" in resp.text and "Get your App ID" in resp.text
    assert "1.50 Wh" in resp.text                         # verified energy from accepted readings


def test_landing_sends_logged_in_users_to_dashboard(client):
    signup(client)
    assert client.get("/", follow_redirects=False).headers["location"] == "/dashboard"


# --- Dashboard -----------------------------------------------------------------

def test_dashboard_requires_login_and_old_wallet_url_redirects(client):
    assert client.get("/dashboard", follow_redirects=False).headers["location"] == "/login"
    assert client.get("/wallet", follow_redirects=False).headers["location"] == "/dashboard"


def test_dashboard_without_meter_invites_to_add_one(client):
    signup(client)
    page = client.get("/dashboard").text
    assert "Add your meter" in page and 'hx-get="/wallet/balances"' in page


def test_dashboard_with_meter_shows_status_and_today(client, engine):
    signup(client)
    add(engine, meter(), reading(1, mwh=150), reading(2, mwh=200), reading(3, decision="rejected", reasons="PHYSICS_LIMIT"))
    page = client.get("/dashboard").text
    assert "VD-0001" in page and "Online" in page          # just reported
    assert "350 mWh" in page                                # energy today = accepted only
    assert "Latest decisions" in page and "PHYSICS" not in page.split("Latest decisions")[0]


def test_power_feed_returns_average_watts_of_accepted_readings(client, engine):
    signup(client)
    add(engine, meter(), reading(1, mwh=150), reading(2, mwh=2460, decision="rejected", reasons="BAD_SIGNATURE"),
        reading(3, mwh=120))
    points = client.get("/api/me/power").json()["points"]
    assert [p["w"] for p in points] == [9.0, 7.2]          # 150 mWh in 60 s = 9 W; the forged 147 W is left out


def test_power_feed_needs_login(client):
    assert client.get("/api/me/power").status_code == 401


# --- Impact ---------------------------------------------------------------------------

def test_impact_is_public_with_formula_and_estimates(client, engine):
    add(engine, User(email="x@y.z", password_hash="x", account_id="0.0.1", encrypted_key="x"), meter(),
        reading(1, mwh=2_000_000), reading(2, decision="rejected", reasons="BAD_SIGNATURE"))
    page = client.get("/impact").text
    assert "2.000 kWh" in page
    assert "0.600" in page                                  # 2 kWh x 0.3 L/kWh
    assert "1.608" in page                                  # 0.6 L x 2.68 kg/L
    assert "L/kWh" in page and "kg/L" in page               # the formula is shown
    assert "BAD_SIGNATURE" in page                          # rejection breakdown
    assert "Get your App ID" in page                        # logged-out call to action


# --- Header account menu ---------------------------------------------------------------

def test_email_and_logout_live_in_the_hidden_account_menu(client):
    signup(client)
    page = client.get("/dashboard").text
    panel = re.search(r'<div id="account-panel" class="([^"]*)">(.*?)</form>', page, re.S)
    assert panel and "hidden" in panel.group(1).split()     # closed until clicked
    assert "ada@example.com" in panel.group(2) and "Log out" in panel.group(2)
    header_outside_panel = page.split('id="account-panel"')[0]
    assert "ada@example.com" not in header_outside_panel


# --- Market layout --------------------------------------------------------------------------

def test_market_subtitle_is_short(client):
    signup(client)
    page = client.get("/market").text
    assert "Trade VKWH for HBAR." in page
    assert "or neither moves" not in page.split("Offers")[0]


def test_order_book_lists_best_price_first(client, engine):
    signup(client, "buyer@example.com")                     # user 1 is the viewer
    add(engine, User(email="s@x.y", password_hash="x", account_id="0.0.777", encrypted_key="x"),
        Offer(seller_id=2, amount_mwh=1_000, price_tinybar=500_000_000),    # 5 HBAR for 0.001 -> 5000/kWh
        Offer(seller_id=2, amount_mwh=1_000, price_tinybar=100_000_000))    # 1 HBAR for 0.001 -> 1000/kWh
    page = client.get("/market").text
    assert page.index("1,000.00") < page.index("5,000.00")
