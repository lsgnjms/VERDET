"""Shared test setup. Tests never touch testnet, verdet.db, or your real secrets."""

import os

# Set these before the app is imported. load_dotenv() never overrides
# variables that already exist, so the values in .env are ignored in tests.
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SESSION_SECRET"] = "test-session-secret"
os.environ["REGISTRY_TOPIC_ID"] = "0.0.1001"
os.environ["READINGS_TOPIC_ID"] = "0.0.1002"
os.environ["TRADES_TOPIC_ID"] = "0.0.1003"
os.environ["VKWH_TOKEN_ID"] = "0.0.1004"

import pytest  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from app import hedera, models  # noqa: E402
from app.main import app  # noqa: E402

FAKE_ACCOUNT_ID = "0.0.999001"
FAKE_PRIVATE_KEY = "302e020100300506032b6570042204201111111111111111111111111111111111111111111111111111111111111111"


class FakeHedera:
    """Stands in for app.hedera's network calls and records what was called."""

    def __init__(self) -> None:
        self.accounts_created = 0
        self.fail = False          # every call fails
        self.fail_mint = False     # only mint/reward fail
        self.fail_hcs = False      # only submit_message fails
        self.balances: dict | None = {"hbar_tinybar": 1_000_000_000, "vkwh_mwh": 0}
        self.messages: list[tuple[str, str]] = []   # (topic_id, message) in order
        self.mints: list[tuple[str, int]] = []      # (token_id, amount_mwh)
        self.rewards: list[tuple[str, str, int]] = []   # (token_id, to_account, amount_mwh)
        self.tx_count = 0
        self.balances_by_account: dict[str, dict] = {}  # per-account overrides of `balances`
        self.swaps: list[tuple] = []                    # atomic_swap arguments
        self.swap_error: str | None = None              # set to make atomic_swap fail

    def atomic_swap(self, token_id, seller_id, seller_key, buyer_id, buyer_key, amount_mwh, price_tinybar) -> str:
        if self.fail or self.swap_error:
            raise hedera.HederaError(self.swap_error or "Hedera is slow or unreachable, try again.")
        self.swaps.append((token_id, seller_id, seller_key, buyer_id, buyer_key, amount_mwh, price_tinybar))
        return self._tx()

    def _tx(self) -> str:
        self.tx_count += 1
        return f"0.0.7179539@1791817388.{self.tx_count:09d}"

    def submit_message(self, topic_id: str, message: str) -> str:
        if self.fail or self.fail_hcs:
            raise hedera.HederaError("Hedera is slow or unreachable, try again.")
        self.messages.append((topic_id, message))
        return self._tx()

    def mint_vkwh(self, token_id: str, amount_mwh: int) -> str:
        if self.fail or self.fail_mint:
            raise hedera.HederaError("Hedera is slow or unreachable, try again.")
        self.mints.append((token_id, amount_mwh))
        return self._tx()

    def reward_vkwh(self, token_id: str, to_account: str, amount_mwh: int) -> str:
        if self.fail or self.fail_mint:
            raise hedera.HederaError("Hedera is slow or unreachable, try again.")
        self.rewards.append((token_id, to_account, amount_mwh))
        return self._tx()

    def create_user_account(self) -> tuple[str, str]:
        if self.fail:
            raise hedera.HederaError("Hedera is slow or unreachable, try again.")
        self.accounts_created += 1
        return f"0.0.99900{self.accounts_created}", FAKE_PRIVATE_KEY

    def get_balances(self, account_id: str, token_id: str | None = None) -> dict | None:
        return self.balances_by_account.get(account_id, self.balances)


@pytest.fixture
def engine():
    """A fresh in-memory database per test."""
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def fake_hedera(monkeypatch) -> FakeHedera:
    fake = FakeHedera()
    monkeypatch.setattr(hedera, "create_user_account", fake.create_user_account)
    monkeypatch.setattr(hedera, "get_balances", fake.get_balances)
    monkeypatch.setattr(hedera, "submit_message", fake.submit_message)
    monkeypatch.setattr(hedera, "mint_vkwh", fake.mint_vkwh)
    monkeypatch.setattr(hedera, "reward_vkwh", fake.reward_vkwh)
    monkeypatch.setattr(hedera, "atomic_swap", fake.atomic_swap)

    def no_network():
        raise AssertionError("A test tried to reach Hedera")

    monkeypatch.setattr(hedera, "get_client", no_network)
    return fake


@pytest.fixture
def client(engine, fake_hedera, monkeypatch):
    """A test browser for the app, wired to the test database and fake Hedera."""
    monkeypatch.setenv("KEY_ENCRYPTION_SECRET", Fernet.generate_key().decode())

    def test_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[models.get_session] = test_session
    yield TestClient(app)
    app.dependency_overrides.clear()
