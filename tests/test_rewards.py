"""Phase 6b: redeem VKWH for airtime/data. Run: pytest tests/test_rewards.py"""

import html as html_lib
import json

import pytest
from sqlmodel import Session, select

from app.main import mask_phone, normalize_phone, voucher_code
from app.models import Offer, Redemption
from tests.conftest import FAKE_PRIVATE_KEY

ACCOUNT = "0.0.999001"
REDEMPTIONS_TOPIC = "0.0.1005"
VKWH_TOKEN = "0.0.1004"


def signup(client, email="ada@example.com"):
    client.post("/signup", data={"email": email, "password": "correct horse"})


def redeem(client, item="AIRTIME_100", network="MTN", phone="0803 123 4567") -> str:
    resp = client.post("/rewards/redeem", data={"item": item, "network": network, "phone": phone})
    return html_lib.unescape(resp.text)


def redemptions(engine) -> list[Redemption]:
    with Session(engine) as session:
        return list(session.exec(select(Redemption)))


@pytest.fixture
def rich(fake_hedera):
    """The user holds 0.002 VKWH (2,000 mWh)."""
    fake_hedera.balances = {"hbar_tinybar": 10**9, "vkwh_mwh": 2_000}
    return fake_hedera


# --- Helpers ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("0803 123 4567", "+2348031234567"), ("08031234567", "+2348031234567"),
    ("+234 803-123-4567", "+2348031234567"), ("2349061234567", "+2349061234567"),
])
def test_normalize_phone_accepts_nigerian_mobiles(text, expected):
    assert normalize_phone(text) == expected


@pytest.mark.parametrize("text", ["12345", "0603 123 4567", "+1 555 123 4567", "", "abc"])
def test_normalize_phone_rejects_others(text):
    assert normalize_phone(text) is None


def test_mask_phone_keeps_only_last_four():
    assert mask_phone("+2348031234567") == "+234******4567"


def test_voucher_code_format():
    code = voucher_code()
    assert len(code) == 12 and code.startswith("VD-") and code[7] == "-"
    assert not set(code) & set("01IO")


# --- Page -------------------------------------------------------------------------------

def test_rewards_requires_login(client):
    assert client.get("/rewards", follow_redirects=False).headers["location"] == "/login"


def test_rewards_page_shows_demo_banner_and_catalogue(client, rich):
    signup(client)
    html = html_lib.unescape(client.get("/rewards").text)
    assert "Testnet demo." in html and "not actually sent" in html
    assert "₦100 airtime" in html and "1 GB data" in html
    assert "Need 0.000800 more" in html                     # 1 GB costs 0.0028, user has 0.002


# --- Redeeming -----------------------------------------------------------------------------

def test_redeem_returns_burns_and_records(client, engine, rich):
    signup(client)
    html = redeem(client)
    assert "Redeemed 0.000500 VKWH for ₦100 airtime (MTN, +234******4567)" in html
    assert "returned and burned on Hedera" in html
    assert rich.returns == [(VKWH_TOKEN, ACCOUNT, FAKE_PRIVATE_KEY, 500)]   # user's key signs
    assert rich.burns == [(VKWH_TOKEN, 500)]
    [row] = redemptions(engine)
    assert (row.item_code, row.amount_mwh, row.network, row.status) == ("AIRTIME_100", 500, "MTN", "completed")
    assert row.phone_masked == "+234******4567" and row.transfer_tx and row.burn_tx and row.hcs_tx
    [(topic, raw)] = [m for m in rich.messages if m[0] == REDEMPTIONS_TOPIC]
    msg = json.loads(raw)
    assert msg["t"] == "redeem" and msg["item"] == "AIRTIME_100" and msg["vkwh"] == "0.000500"
    assert msg["delivery"] == "demo" and msg["burnTx"] == row.burn_tx
    assert "4567" not in raw and "phone" not in msg              # no phone number on-chain


def test_cannot_redeem_more_than_available(client, engine, rich):
    signup(client)
    html = redeem(client, item="DATA_1GB")                          # 2,800 mWh > 2,000
    assert "you have 0.002000 VKWH available" in html
    assert rich.returns == [] and redemptions(engine) == []


def test_vkwh_listed_for_sale_cannot_be_redeemed(client, engine, rich):
    signup(client)
    with Session(engine) as session:
        session.add(Offer(seller_id=1, amount_mwh=1_800, price_tinybar=10**6))
        session.commit()
    assert "you have 0.000200 VKWH available" in redeem(client)   # 2,000 - 1,800 listed
    assert rich.returns == []


@pytest.mark.parametrize("data, message", [
    ({"item": "AIRTIME_9999"}, "Pick a reward"),
    ({"network": "Vodafone"}, "Pick your mobile network"),
    ({"phone": "12345"}, "Nigerian mobile number"),
])
def test_bad_input_is_refused_before_hedera(client, engine, rich, data, message):
    signup(client)
    assert message in redeem(client, **data)
    assert rich.returns == [] and redemptions(engine) == []


def test_failed_return_moves_nothing(client, engine, rich):
    signup(client)
    rich.fail_mint = True                                         # fake: transfers fail
    assert "nothing moved" in redeem(client)
    assert redemptions(engine) == []


def test_failed_burn_is_recorded_as_pending(client, engine, rich):
    signup(client)
    rich.fail_burn = True
    redeem(client)
    [row] = redemptions(engine)
    assert row.status == "burn_pending" and row.burn_tx is None and row.transfer_tx
    assert "burn pending" in client.get("/rewards").text


def test_history_shows_voucher_and_proofs(client, engine, rich):
    signup(client)
    redeem(client)
    [row] = redemptions(engine)
    html = client.get("/rewards").text
    assert row.voucher_code in html and "+234******4567" in html
    assert html.count("hashscan.io/testnet/transaction/") >= 3      # return, burn, record


def test_impact_counts_redemptions(client, engine, rich):
    signup(client)
    redeem(client)
    html = client.get("/impact").text
    assert "Rewards redeemed" in html and "0.000500 VKWH spent on airtime and data" in html
