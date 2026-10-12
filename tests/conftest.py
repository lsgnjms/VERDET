"""Shared test setup. Tests never touch testnet, verdet.db, or your real secrets."""

import os

# Set these before the app is imported. load_dotenv() never overrides
# variables that already exist, so the values in .env are ignored in tests.
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SESSION_SECRET"] = "test-session-secret"

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
        self.fail = False
        self.balances: dict | None = {"hbar_tinybar": 1_000_000_000, "vkwh_mwh": 0}

    def create_user_account(self) -> tuple[str, str]:
        if self.fail:
            raise hedera.HederaError("Hedera is slow or unreachable, try again.")
        self.accounts_created += 1
        return f"0.0.99900{self.accounts_created}", FAKE_PRIVATE_KEY

    def get_balances(self, account_id: str, token_id: str | None = None) -> dict | None:
        return self.balances


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
