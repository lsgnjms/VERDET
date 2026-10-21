"""Database tables and connection.

SQLite locally, Postgres in production; DATABASE_URL picks which.
Amounts are integers: energy in mWh, power in mW, HBAR in tinybars.
"""

import os
from collections.abc import Iterator
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import Index, inspect, text
from sqlalchemy.exc import ArgumentError
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
    sig: str | None = None                              # the meter's Ed25519 signature (hex), for re-verification
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


class Redemption(SQLModel, table=True):
    """VKWH spent on a reward (airtime/data). The VKWH really goes back to the treasury
    and is burned on Hedera; the airtime/data voucher itself is a demo."""

    __tablename__ = "redemptions"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    item_code: str                                      # e.g. "AIRTIME_100"
    item_label: str                                     # e.g. "₦100 airtime"
    amount_mwh: int
    network: str                                        # MTN / Airtel / Glo / 9mobile
    phone_masked: str                                   # only the last 4 digits are kept
    voucher_code: str                                   # demo voucher
    status: str = "completed"                           # completed / burn_pending
    transfer_tx: str | None = None                      # user -> treasury
    burn_tx: str | None = None                          # treasury burn
    hcs_tx: str | None = None                           # redemptions topic record
    created_at: datetime = Field(default_factory=utc_now)


# --- Connection -------------------------------------------------------------

def database_url(raw: str) -> str:
    """Neon/Render give 'postgres://...' or 'postgresql://...'; SQLAlchemy needs the
    driver named: 'postgresql+psycopg://...'. SQLite URLs pass through unchanged.

    Also forgives common copy-paste extras: spaces, quotes, and Neon's
    "psql 'postgresql://...'" command form."""
    url = raw.strip().strip("'\"")
    if url.startswith("psql "):
        url = url[len("psql "):].strip().strip("'\"")
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def make_engine(url: str):
    """Create the engine, with a clear message (and no secret in it) if the URL is bad."""
    try:
        if url.startswith("sqlite"):
            # SQLite needs this so FastAPI's worker threads can share the connection.
            return create_engine(url, connect_args={"check_same_thread": False})
        # Neon closes idle connections; pre_ping replaces dead ones instead of erroring.
        return create_engine(url, pool_pre_ping=True, pool_recycle=300)
    except ArgumentError:
        raise RuntimeError(
            "DATABASE_URL isn't a valid database URL. Paste only the connection string that "
            "starts with postgresql://, with no 'psql', quotes or spaces around it."
        ) from None


DATABASE_URL = database_url(os.getenv("DATABASE_URL", "sqlite:///./verdet.db"))
engine = make_engine(DATABASE_URL)


def create_db() -> None:
    """Create any missing tables, then add any columns added since a table was created.
    (Only additive, nullable columns; anything bigger: delete verdet.db to reset locally.)"""
    SQLModel.metadata.create_all(engine)
    add_missing_columns()


ADDED_COLUMNS = {"readings": {"sig": "VARCHAR"}}   # table -> {column: SQL type}, added Oct 19


def add_missing_columns() -> None:
    existing = inspect(engine)
    with engine.begin() as conn:
        for table, columns in ADDED_COLUMNS.items():
            have = {c["name"] for c in existing.get_columns(table)}
            for name, sql_type in columns.items():
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one database session per request."""
    with Session(engine) as session:
        yield session
