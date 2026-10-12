"""Database tables and connection.

SQLite locally, Postgres in production; DATABASE_URL picks which.
Amounts are integers: energy in mWh, power in mW, HBAR in tinybars.
"""

import os
from collections.abc import Iterator
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import Index, text
from sqlmodel import Field, Session, SQLModel, create_engine

load_dotenv()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class User(SQLModel, table=True):
    """A person who logs in. Their App ID is their Hedera account ID."""

    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)        # stored lowercase
    password_hash: str                                  # argon2, never the password
    account_id: str = Field(unique=True, index=True)   # e.g. "0.0.5123456"
    encrypted_key: str                                  # Fernet token of the DER hex key
    created_at: datetime = Field(default_factory=utc_now)


class Meter(SQLModel, table=True):
    """A registered meter and the public key its readings must be signed with."""

    __tablename__ = "meters"

    id: int | None = Field(default=None, primary_key=True)
    meter_id: str = Field(unique=True, index=True)     # e.g. "VD-0001"
    owner_id: int = Field(foreign_key="users.id", index=True)
    pubkey_hex: str                                     # Ed25519 public key, 64 hex chars
    rated_w: int                                        # panel rating in watts
    lat: float
    lon: float
    last_seq: int = -1                                  # first reading may be seq 0
    created_at: datetime = Field(default_factory=utc_now)


class Reading(SQLModel, table=True):
    """Every reading the app received, accepted or rejected (rule 7).

    meter_id is deliberately not a foreign key: UNREGISTERED readings name a
    meter that doesn't exist, and they are stored too.
    """

    __tablename__ = "readings"
    # Database backstop for races: only one *accepted* reading per (meter, seq).
    # Rejected readings may repeat a seq (that's what a replay looks like).
    __table_args__ = (
        Index(
            "uq_accepted_meter_seq",
            "meter_id",
            "seq",
            unique=True,
            sqlite_where=text("decision = 'accepted'"),
            postgresql_where=text("decision = 'accepted'"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    meter_id: str = Field(index=True)
    seq: int
    t_start_ms: int
    t_end_ms: int
    mwh: int
    peak_mw: int
    avg_mv: int
    avg_ma: int
    hash: str                                           # sha256 of the canonical string
    decision: str                                       # "accepted" or "rejected"
    reasons: str = ""                                   # comma-separated codes, e.g. "REPLAY"
    ai_score: float | None = None
    hcs_tx: str | None = None                           # readings-topic transaction ID
    mint_tx: str | None = None                          # set only for accepted readings
    created_at: datetime = Field(default_factory=utc_now)


class Offer(SQLModel, table=True):
    """A fixed-price marketplace offer: amount_mwh of VKWH for price_tinybar HBAR."""

    __tablename__ = "offers"

    id: int | None = Field(default=None, primary_key=True)
    seller_id: int = Field(foreign_key="users.id", index=True)
    amount_mwh: int
    price_tinybar: int
    status: str = Field(default="open", index=True)    # open / filled / cancelled
    buyer_id: int | None = Field(default=None, foreign_key="users.id")
    tx_id: str | None = None                            # the atomic swap transaction
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


# --- Connection -------------------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./verdet.db")
# SQLite needs this so FastAPI's worker threads can share the connection.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)


def create_db() -> None:
    """Create any missing tables. No migrations: delete verdet.db to reset locally."""
    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one database session per request."""
    with Session(engine) as session:
        yield session
