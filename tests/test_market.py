"""Phase 6: marketplace with atomic swaps. Run: pytest tests/test_market.py"""

import html as html_lib
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.main import app, parse_units
from app.models import Offer
from tests.conftest import FAKE_PRIVATE_KEY

SELLER, BUYER = "0.0.999001", "0.0.999002"   # App IDs the fake gives the 1st and 2nd signup
TRADES_TOPIC, VKWH_TOKEN = "0.0.1003", "0.0.1004"


def signup(client, email):
    client.post("/signup", data={"email": email, "password": "correct horse"})


@pytest.fixture
def market(client, fake_hedera):
    """Seller (1 VKWH, 10 HBAR) and buyer (0 VKWH, 10 HBAR), each in their own browser."""
    fake_hedera.balances_by_account = {
        SELLER: {"hbar_tinybar": 10 * 10**8, "vkwh_mwh": 1_000_000},
        BUYER: {"hbar_tinybar": 10 * 10**8, "vkwh_mwh": 0},
    }
    signup(client, "seller@example.com")
    buyer = TestClient(app)
    signup(buyer, "buyer@example.com")
    return client, buyer


def offers(engine) -> list[Offer]:
    with Session(engine) as session:
        return list(session.exec(select(Offer).order_by(Offer.id)))


class Page:
    """A response whose .text has HTML entities decoded (the page escapes "can't" as can&#39;t)."""
    def __init__(self, resp):
        self.text = html_lib.unescape(resp.text)


def list_offer(client, amount="0.5", price="4") -> Page:
    """price is HBAR per kWh: 0.5 VKWH at 4 HBAR/kWh = 2 HBAR total."""
    return Page(client.post("/offers", data={"amount_vkwh": amount, "price_per_kwh": price}, follow_redirects=True))


def act(client, offer_id: int, action: str) -> str:
    """POST buy/cancel, follow the redirect, return the decoded page text."""
    return Page(client.post(f"/offers/{offer_id}/{action}", follow_redirects=True)).text


# --- Parsing money -------------------------------------------------------------

@pytest.mark.parametrize("text, decimals, expected", [
    ("0.5", 6, 500_000), ("1", 6, 1_000_000), ("0.000001", 6, 1), ("2", 8, 200_000_000), (" 1.25 ", 8, 125_000_000),
])
def test_parse_units_exact(text, decimals, expected):
    assert parse_units(text, decimals) == expected


@pytest.mark.parametrize("text", ["0", "-1", "abc", "", "0.0000001", "NaN", "Infinity", "1e400"])
def test_parse_units_rejects_bad_input(text):
    assert parse_units(text, 6) is None


# --- Listing --------------------------------------------------------------------

def test_market_requires_login(client):
    assert client.get("/market", follow_redirects=False).headers["location"] == "/login"
    resp = client.post("/offers", data={"amount_vkwh": "1", "price_per_kwh": "10"}, follow_redirects=False)
    assert resp.headers["location"] == "/login"


def test_list_offer(market, engine):
    seller, _ = market
    html = list_offer(seller).text
    assert "Listed 0.500000 VKWH at 4.00 HBAR/kWh. You'll receive 2.00 HBAR" in html
    [offer] = offers(engine)
    assert (offer.amount_mwh, offer.price_tinybar, offer.status) == (500_000, 200_000_000, "open")


def test_cannot_list_more_than_balance(market, engine):
    seller, _ = market
    assert "at most 1.000000 VKWH" in list_offer(seller, amount="1.5").text
    assert offers(engine) == []


def test_cannot_list_the_same_energy_twice(market, engine):
    seller, _ = market
    list_offer(seller, amount="0.8")
    assert "at most 0.200000 VKWH" in list_offer(seller, amount="0.5").text
    assert len(offers(engine)) == 1


def test_bad_amounts_rejected(market, engine):
    seller, _ = market
    assert "Amount must be" in list_offer(seller, amount="-1").text
    assert "Price per kWh must be" in list_offer(seller, price="abc").text
    assert offers(engine) == []


def test_listing_refused_when_balance_unknown(market, engine, fake_hedera):
    seller, _ = market
    fake_hedera.balances_by_account = {}
    fake_hedera.balances = None
    assert "Couldn't check your VKWH balance" in list_offer(seller).text
    assert offers(engine) == []


# --- Buying: the Phase 6 checkpoint ----------------------------------------------

def test_buy_is_one_atomic_swap_signed_by_both(market, engine, fake_hedera):
    seller, buyer = market
    list_offer(seller)
    offer_id = offers(engine)[0].id
    html = act(buyer, offer_id, "buy")

    assert "ONE Hedera transaction" in html and "hashscan.io/testnet/transaction/" in html
    [swap] = fake_hedera.swaps
    # VKWH token, seller + seller's decrypted key, buyer + buyer's decrypted key, amount, price
    assert swap == (VKWH_TOKEN, SELLER, FAKE_PRIVATE_KEY, BUYER, FAKE_PRIVATE_KEY, 500_000, 200_000_000)
    [offer] = offers(engine)
    assert offer.status == "filled" and offer.tx_id and offer.buyer_id

    [(topic, raw)] = [m for m in fake_hedera.messages if m[0] == TRADES_TOPIC]
    msg = json.loads(raw)
    assert msg == {"t": "trade", "v": 1, "offerId": offer.id, "seller": SELLER, "buyer": BUYER,
                   "vkwh": "0.500000", "hbar": "2.00", "tx": offer.tx_id}
    assert len(raw.encode()) < 1024


def test_second_buy_fails_with_clear_message(market, engine, fake_hedera):
    seller, buyer = market
    list_offer(seller)
    offer_id = offers(engine)[0].id
    buyer.post(f"/offers/{offer_id}/buy")
    html = act(buyer, offer_id, "buy")
    assert "no longer available" in html
    assert len(fake_hedera.swaps) == 1


def test_cannot_buy_own_offer(market, engine, fake_hedera):
    seller, _ = market
    list_offer(seller)
    html = act(seller, offers(engine)[0].id, "buy")
    assert "can't buy your own offer" in html
    assert fake_hedera.swaps == [] and offers(engine)[0].status == "open"


def test_buyer_without_enough_hbar_is_refused(market, engine, fake_hedera):
    seller, buyer = market
    fake_hedera.balances_by_account[BUYER] = {"hbar_tinybar": 1 * 10**8, "vkwh_mwh": 0}
    list_offer(seller)
    html = act(buyer, offers(engine)[0].id, "buy")
    assert "enough HBAR" in html
    assert fake_hedera.swaps == [] and offers(engine)[0].status == "open"


def test_failed_swap_reopens_the_offer(market, engine, fake_hedera):
    seller, buyer = market
    list_offer(seller)
    fake_hedera.swap_error = "Hedera is slow or unreachable, try again. (MaxAttemptsError)"
    html = act(buyer, offers(engine)[0].id, "buy")
    assert "nothing moved" in html
    [offer] = offers(engine)
    assert offer.status == "open" and offer.buyer_id is None


def test_seller_out_of_vkwh_cancels_the_offer(market, engine, fake_hedera):
    seller, buyer = market
    list_offer(seller)
    fake_hedera.swap_error = "Hedera rejected the transaction: INSUFFICIENT_TOKEN_BALANCE"
    html = act(buyer, offers(engine)[0].id, "buy")
    assert "seller no longer has enough VKWH" in html
    assert offers(engine)[0].status == "cancelled"


# --- Cancel and page ------------------------------------------------------------------

def test_cancel_own_offer_only(market, engine):
    seller, buyer = market
    list_offer(seller)
    offer_id = offers(engine)[0].id
    assert "only cancel your own" in act(buyer, offer_id, "cancel")
    assert "Offer cancelled" in act(seller, offer_id, "cancel")
    assert offers(engine)[0].status == "cancelled"
    assert "no longer available" in act(buyer, offer_id, "buy")


def test_page_shows_others_offers_and_trade_history(market, engine):
    seller, buyer = market
    list_offer(seller)
    assert SELLER in buyer.get("/market").text            # buyer sees the seller's offer
    assert "No offers from other users" in seller.get("/market").text
    buyer.post(f"/offers/{offers(engine)[0].id}/buy")
    html = buyer.get("/market").text
    assert f"{SELLER} → {BUYER}" in html and "1 tx" in html
