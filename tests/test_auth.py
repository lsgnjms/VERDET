"""Phase 1: sign-up, log-in, log-out, wallet card. Run: pytest tests/test_auth.py"""

from sqlmodel import Session, select

from app.auth import decrypt_key, verify_password
from app.models import User
from tests.conftest import FAKE_PRIVATE_KEY

PASSWORD = "correct horse"


def signup(client, email="ada@example.com", password=PASSWORD):
    return client.post("/signup", data={"email": email, "password": password}, follow_redirects=False)


def all_users(engine) -> list[User]:
    with Session(engine) as session:
        return list(session.exec(select(User)))


# --- Sign-up ---------------------------------------------------------------

def test_signup_creates_user_and_logs_in(client, engine, fake_hedera):
    resp = signup(client)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/wallet"
    assert fake_hedera.accounts_created == 1
    [user] = all_users(engine)
    assert user.account_id == "0.0.999001"
    # Logged in straight away: the wallet opens instead of redirecting.
    assert client.get("/wallet", follow_redirects=False).status_code == 200


def test_signup_stores_only_encrypted_key(client, engine):
    signup(client)
    [user] = all_users(engine)
    assert user.encrypted_key.startswith("gAAAA")
    assert FAKE_PRIVATE_KEY not in user.encrypted_key
    assert decrypt_key(user.encrypted_key) == FAKE_PRIVATE_KEY


def test_signup_stores_only_password_hash(client, engine):
    signup(client)
    [user] = all_users(engine)
    assert PASSWORD not in user.password_hash
    assert user.password_hash.startswith("$argon2")
    assert verify_password(user.password_hash, PASSWORD)


def test_signup_stores_email_lowercase(client, engine):
    signup(client, email="  Ada@Example.COM ")
    [user] = all_users(engine)
    assert user.email == "ada@example.com"


def test_two_users_get_different_app_ids(client, engine):
    signup(client, email="ada@example.com")
    client.post("/logout")
    signup(client, email="grace@example.com")
    ids = {u.account_id for u in all_users(engine)}
    assert len(ids) == 2


def test_duplicate_email_rejected_without_calling_hedera(client, engine, fake_hedera):
    signup(client, email="ada@example.com")
    client.post("/logout")
    resp = signup(client, email="ADA@example.com")
    assert resp.status_code == 400
    assert "already exists" in resp.text
    assert fake_hedera.accounts_created == 1   # the second signup cost no HBAR
    assert len(all_users(engine)) == 1


def test_invalid_email_rejected_without_calling_hedera(client, engine, fake_hedera):
    resp = signup(client, email="not-an-email")
    assert resp.status_code == 400
    assert fake_hedera.accounts_created == 0
    assert all_users(engine) == []


def test_short_password_rejected_without_calling_hedera(client, engine, fake_hedera):
    resp = signup(client, password="short")
    assert resp.status_code == 400
    assert "at least 8" in resp.text
    assert fake_hedera.accounts_created == 0
    assert all_users(engine) == []


def test_hedera_failure_creates_no_user(client, engine, fake_hedera):
    fake_hedera.fail = True
    resp = signup(client)
    assert resp.status_code == 503
    assert "try again" in resp.text
    assert all_users(engine) == []
    assert client.get("/wallet", follow_redirects=False).headers["location"] == "/login"


# --- Log-in / log-out ------------------------------------------------------

def test_login_with_right_password(client):
    signup(client)
    client.post("/logout")
    resp = client.post("/login", data={"email": "Ada@Example.com", "password": PASSWORD}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/wallet"
    assert client.get("/wallet", follow_redirects=False).status_code == 200


def test_login_with_wrong_password_fails(client):
    signup(client)
    client.post("/logout")
    resp = client.post("/login", data={"email": "ada@example.com", "password": "wrong password"})
    assert resp.status_code == 400
    assert "Wrong email or password" in resp.text
    assert client.get("/wallet", follow_redirects=False).headers["location"] == "/login"


def test_login_with_unknown_email_gives_same_message(client):
    resp = client.post("/login", data={"email": "nobody@example.com", "password": PASSWORD})
    assert resp.status_code == 400
    assert "Wrong email or password" in resp.text


def test_logout_clears_session(client):
    signup(client)
    resp = client.post("/logout", follow_redirects=False)
    assert resp.headers["location"] == "/login"
    assert client.get("/wallet", follow_redirects=False).headers["location"] == "/login"


# --- Wallet card -----------------------------------------------------------

def test_wallet_requires_login(client):
    assert client.get("/wallet", follow_redirects=False).headers["location"] == "/login"
    assert client.get("/wallet/balances").status_code == 401


def test_wallet_shows_app_id_and_hashscan_link(client):
    signup(client)
    html = client.get("/wallet").text
    assert "0.0.999001" in html
    assert "https://hashscan.io/testnet/account/0.0.999001" in html


def test_balances_partial_shows_hbar_and_vkwh(client, fake_hedera):
    fake_hedera.balances = {"hbar_tinybar": 1_000_000_000, "vkwh_mwh": 500_000}
    signup(client)
    html = client.get("/wallet/balances").text
    assert "10.00" in html
    assert "0.500000" in html


def test_balances_partial_when_mirror_node_not_ready(client, fake_hedera):
    fake_hedera.balances = None
    signup(client)
    html = client.get("/wallet/balances").text
    assert "not available yet" in html
