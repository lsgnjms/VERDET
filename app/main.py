"""VERDET web app: routes and pages.

Run: uvicorn app.main:app --reload   (then open http://127.0.0.1:8000)
Routes are plain `def` because the Hedera SDK is synchronous; FastAPI runs
them in a thread pool so a slow Hedera call doesn't block other requests.
"""

import json
import os
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Body, Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select
from starlette.middleware.sessions import SessionMiddleware

from app import hedera
from app.auth import (
    current_user,
    decrypt_key,
    encrypt_key,
    hash_password,
    log_in,
    log_out,
    normalize_email,
    verify_password,
)
from app.models import Meter, Offer, Reading, User, create_db, get_session, utc_now
from app.verify import DUPLICATE, Decision, verify_reading
from gateway.canonical import METER_ID_PATTERN, canonical_string

load_dotenv()

MIN_PASSWORD_LENGTH = 8
MAX_RATED_W = 10_000          # prototype: small DC panels only
READINGS_SHOWN = 50           # rows in the readings table
LAGOS = timezone(timedelta(hours=1))   # WAT, no daylight saving


def _session_secret() -> str:
    secret = os.getenv("SESSION_SECRET", "").strip()
    if not secret or secret == "change-me":
        raise RuntimeError("Set SESSION_SECRET in .env (see .env.example for how to generate one).")
    return secret


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db()   # create any missing tables at startup
    yield


app = FastAPI(title="VERDET", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=_session_secret(), same_site="lax")

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


# --- Display helpers (integers in, readable text out; no float rounding) ----

def format_vkwh(mwh: int) -> str:
    """500000 -> '0.500000' (1 VKWH = 1,000,000 mWh units)."""
    return f"{mwh // 1_000_000}.{mwh % 1_000_000:06d}"


def format_hbar(tinybar: int) -> str:
    """1000000000 -> '10.00', 1234567890 -> '12.3456789'."""
    whole, frac = divmod(tinybar, 100_000_000)
    frac_str = f"{frac:08d}".rstrip("0").ljust(2, "0")
    return f"{whole}.{frac_str}"


def format_lagos_time(moment: datetime) -> str:
    """Stored times are UTC (SQLite drops the timezone); show them in Lagos time."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(LAGOS).strftime("%d %b %H:%M:%S")


templates.env.filters["vkwh"] = format_vkwh
templates.env.filters["hbar"] = format_hbar
templates.env.filters["lagos_time"] = format_lagos_time
templates.env.globals["hashscan_account"] = hedera.hashscan_account
templates.env.globals["hashscan_tx"] = hedera.hashscan_tx

CurrentUser = Annotated[User | None, Depends(current_user)]
DbSession = Annotated[Session, Depends(get_session)]


# --- Pages ------------------------------------------------------------------

@app.get("/")
def home(user: CurrentUser) -> RedirectResponse:
    return RedirectResponse("/wallet" if user else "/login", status_code=303)


@app.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request, user: CurrentUser):
    if user:
        return RedirectResponse("/wallet", status_code=303)
    return templates.TemplateResponse(request, "signup.html", {"user": None})


@app.post("/signup", response_class=HTMLResponse)
def signup(
    request: Request,
    db: DbSession,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    email = normalize_email(email)

    def show_error(message: str, status: int):
        return templates.TemplateResponse(
            request, "signup.html", {"user": None, "error": message, "email": email}, status_code=status
        )

    if "@" not in email or "." not in email.split("@")[-1]:
        return show_error("Please enter a valid email address.", 400)
    if len(password) < MIN_PASSWORD_LENGTH:
        return show_error(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.", 400)
    # Check before calling Hedera, so a taken email never costs HBAR.
    if db.exec(select(User).where(User.email == email)).first():
        return show_error("An account with this email already exists. Log in instead.", 400)

    try:
        account_id, private_key = hedera.create_user_account()
    except hedera.HederaError as exc:
        return show_error(f"Couldn't create your Hedera account: {exc}", 503)

    user = User(
        email=email,
        password_hash=hash_password(password),
        account_id=account_id,
        encrypted_key=encrypt_key(private_key),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # Two signups with the same email at the same moment.
        db.rollback()
        return show_error("An account with this email already exists. Log in instead.", 400)
    db.refresh(user)
    log_in(request, user)
    return RedirectResponse("/wallet", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, user: CurrentUser):
    if user:
        return RedirectResponse("/wallet", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"user": None})


@app.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    db: DbSession,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    email = normalize_email(email)
    user = db.exec(select(User).where(User.email == email)).first()
    if not user or not verify_password(user.password_hash, password):
        # Same message either way, so the form doesn't reveal which emails exist.
        return templates.TemplateResponse(
            request, "login.html", {"user": None, "error": "Wrong email or password.", "email": email},
            status_code=400,
        )
    log_in(request, user)
    return RedirectResponse("/wallet", status_code=303)


@app.post("/logout")
def logout(request: Request) -> RedirectResponse:
    log_out(request)
    return RedirectResponse("/login", status_code=303)


@app.get("/wallet", response_class=HTMLResponse)
def wallet(request: Request, user: CurrentUser):
    if not user:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "wallet.html", {"user": user})


@app.get("/wallet/balances", response_class=HTMLResponse)
def wallet_balances(request: Request, user: CurrentUser):
    """HTMX partial: the balance lines inside the wallet card."""
    if not user:
        return Response(status_code=401)
    balances = hedera.get_balances(user.account_id)
    return templates.TemplateResponse(request, "_balances.html", {"balances": balances})


# --- Readings page ------------------------------------------------------------

def user_readings_context(db: Session, user: User) -> dict:
    """The latest readings from this user's meters, plus accepted totals."""
    meter_ids = list(db.exec(select(Meter.meter_id).where(Meter.owner_id == user.id)))
    if not meter_ids:
        return {"readings": [], "accepted_mwh": 0, "accepted_count": 0, "has_meters": False}
    readings = db.exec(
        select(Reading).where(Reading.meter_id.in_(meter_ids)).order_by(Reading.id.desc()).limit(READINGS_SHOWN)
    ).all()
    accepted_mwh, accepted_count = db.exec(
        select(func.coalesce(func.sum(Reading.mwh), 0), func.count(Reading.id))
        .where(Reading.meter_id.in_(meter_ids), Reading.decision == "accepted")
    ).one()
    # Rewarded = accepted AND the mint went through (differs if Hedera was down).
    rewarded_mwh = db.exec(
        select(func.coalesce(func.sum(Reading.mwh), 0))
        .where(Reading.meter_id.in_(meter_ids), Reading.decision == "accepted", Reading.mint_tx.is_not(None))
    ).one()
    return {"readings": readings, "accepted_mwh": accepted_mwh, "accepted_count": accepted_count,
            "rewarded_mwh": rewarded_mwh, "has_meters": True}


@app.get("/readings", response_class=HTMLResponse)
def readings_page(request: Request, user: CurrentUser):
    if not user:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "readings.html", {"user": user})


@app.get("/readings/table", response_class=HTMLResponse)
def readings_table(request: Request, db: DbSession, user: CurrentUser):
    """HTMX partial: the readings table, refreshed every 5 s."""
    if not user:
        return Response(status_code=401)
    return templates.TemplateResponse(request, "_readings_table.html", user_readings_context(db, user))


# --- Meters -----------------------------------------------------------------

def parse_meter_form(meter_id: str, pubkey_hex: str, rated_w: str, lat: str, lon: str) -> tuple[dict, str | None]:
    """Clean up the Add-meter form. Returns (values, error message or None)."""
    values = {
        "meter_id": meter_id.strip(),
        "pubkey_hex": pubkey_hex.strip().lower(),
        "rated_w": rated_w.strip(),
        "lat": lat.strip(),
        "lon": lon.strip(),
    }
    if not METER_ID_PATTERN.match(values["meter_id"]):
        return values, "Meter ID must be 1-32 letters, digits or dashes (e.g. VD-0001)."
    key = values["pubkey_hex"]
    if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        return values, "Public key must be the 64 hex characters the meter prints as PUB."
    try:
        rated = int(values["rated_w"])
        lat_f, lon_f = float(values["lat"]), float(values["lon"])
    except ValueError:
        return values, "Rated watts must be a whole number; latitude and longitude must be numbers."
    if not 1 <= rated <= MAX_RATED_W:
        return values, f"Rated watts must be between 1 and {MAX_RATED_W}."
    if not (-90 <= lat_f <= 90 and -180 <= lon_f <= 180):
        return values, "Latitude must be -90 to 90 and longitude -180 to 180."
    values.update(rated_w=rated, lat=lat_f, lon=lon_f)
    return values, None


def registry_message(meter: Meter, owner_account: str) -> str:
    """The `registry` HCS message (Plan.md schema), compact JSON, under 1 KB."""
    return json.dumps(
        {"t": "meter.register", "v": 1, "meterId": meter.meter_id, "owner": owner_account,
         "pubKey": meter.pubkey_hex, "ratedW": meter.rated_w, "lat": meter.lat, "lon": meter.lon},
        separators=(",", ":"),
    )


def render_meters(request: Request, db: Session, user: User, status: int = 200, **extra):
    meters = db.exec(select(Meter).where(Meter.owner_id == user.id).order_by(Meter.created_at)).all()
    context = {"user": user, "meters": meters, "form": {"lat": "6.52", "lon": "3.38"}, **extra}
    return templates.TemplateResponse(request, "meters.html", context, status_code=status)


@app.get("/meters", response_class=HTMLResponse)
def meters_page(request: Request, db: DbSession, user: CurrentUser):
    if not user:
        return RedirectResponse("/login", status_code=303)
    return render_meters(request, db, user)


@app.post("/meters", response_class=HTMLResponse)
def add_meter(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    meter_id: Annotated[str, Form()],
    pubkey_hex: Annotated[str, Form()],
    rated_w: Annotated[str, Form()],
    lat: Annotated[str, Form()],
    lon: Annotated[str, Form()],
):
    if not user:
        return RedirectResponse("/login", status_code=303)
    values, error = parse_meter_form(meter_id, pubkey_hex, rated_w, lat, lon)
    if error:
        return render_meters(request, db, user, 400, error=error, form=values)
    # Check before calling Hedera, so a rejected form never costs a fee.
    if db.exec(select(Meter).where(Meter.meter_id == values["meter_id"])).first():
        return render_meters(request, db, user, 400, error="That meter ID is already registered.", form=values)
    if db.exec(select(Meter).where(Meter.pubkey_hex == values["pubkey_hex"])).first():
        return render_meters(
            request, db, user, 400, form=values,
            error="That public key is already registered to another meter. One device, one meter.",
        )

    meter = Meter(owner_id=user.id, **values)
    try:
        tx_id = hedera.submit_message(hedera.config_id("REGISTRY_TOPIC_ID"), registry_message(meter, user.account_id))
    except hedera.HederaError as exc:
        return render_meters(request, db, user, 503, error=f"Couldn't record the meter on Hedera: {exc}", form=values)

    db.add(meter)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render_meters(request, db, user, 400, error="That meter ID is already registered.", form=values)
    return render_meters(request, db, user, success=meter.meter_id, registry_url=hedera.hashscan_tx(tx_id))


# --- Readings API -----------------------------------------------------------

def save_reading(db: Session, reading: dict, decision: Decision) -> Reading:
    """Store one reading with its decision (accepted and rejected alike)."""
    row = Reading(
        meter_id=reading["meterId"],
        seq=reading["seq"],
        t_start_ms=reading["tStartMs"],
        t_end_ms=reading["tEndMs"],
        mwh=reading["mWh"],
        peak_mw=reading["peakMw"],
        avg_mv=reading["avgMv"],
        avg_ma=reading["avgMa"],
        hash=decision.hash,
        decision=decision.decision,
        reasons=",".join(decision.reasons),
    )
    db.add(row)
    return row


def reward_owner(db: Session, row: Reading) -> tuple[str, str]:
    """Mint the reading's mWh as VKWH into the treasury, then send it to the
    meter's owner. Returns (mint_tx, reward_tx). Raises HederaError."""
    meter = db.exec(select(Meter).where(Meter.meter_id == row.meter_id)).one()
    owner = db.get(User, meter.owner_id)
    token_id = hedera.config_id("VKWH_TOKEN_ID")
    mint_tx = hedera.mint_vkwh(token_id, row.mwh)
    reward_tx = hedera.reward_vkwh(token_id, owner.account_id, row.mwh)
    return mint_tx, reward_tx


def readings_message(row: Reading, reward_tx: str | None) -> str:
    """The `readings` HCS message (Plan.md schema), compact JSON, under 1 KB."""
    msg = {
        "t": "reading", "v": 1, "meterId": row.meter_id, "seq": row.seq,
        "mWh": row.mwh, "peakMw": row.peak_mw, "hash": row.hash,
        "decision": row.decision, "reasons": row.reasons.split(",") if row.reasons else [],
    }
    if row.decision == "accepted":
        msg["mintTx"] = row.mint_tx
        msg["rewardTx"] = reward_tx
    return json.dumps(msg, separators=(",", ":"))


def anchor_and_reward(db: Session, row: Reading) -> dict:
    """After the decision is saved: reward accepted energy, then record the
    decision on the `readings` topic (rule 6). Hedera failures never undo the
    decision; the missing tx ID shows up as "pending" instead."""
    result: dict = {"mintTx": None, "rewardTx": None, "hcsTx": None}
    errors = []
    if row.decision == "accepted" and row.mwh > 0:
        try:
            row.mint_tx, result["rewardTx"] = reward_owner(db, row)
            result["mintTx"] = row.mint_tx
        except hedera.HederaError as exc:
            errors.append(f"Reward not sent: {exc}")
    try:
        row.hcs_tx = hedera.submit_message(
            hedera.config_id("READINGS_TOPIC_ID"), readings_message(row, result["rewardTx"])
        )
        result["hcsTx"] = row.hcs_tx
    except hedera.HederaError as exc:
        errors.append(f"Decision not recorded on Hedera: {exc}")
    db.add(row)
    db.commit()
    if errors:
        result["hederaError"] = " ".join(errors)
    return result


@app.post("/api/readings")
def receive_reading(db: DbSession, reading: Annotated[dict, Body()]) -> JSONResponse:
    """Receive one signed reading from a gateway, verify it, and store the decision.

    No login: the meter's signature is what proves who sent it.
    """
    try:
        canonical_string(reading)
    except ValueError as exc:
        # Malformed: not a decision, nothing is stored.
        return JSONResponse({"error": str(exc)}, status_code=422)

    meter = db.exec(select(Meter).where(Meter.meter_id == reading["meterId"])).first()
    decision = verify_reading(reading, meter)
    if meter and decision.new_last_seq is not None:
        meter.last_seq = decision.new_last_seq
        db.add(meter)
    row = save_reading(db, reading, decision)
    try:
        db.commit()
    except IntegrityError:
        # Race backstop (rule 7): another copy of this (meter, seq) was accepted
        # at the same moment. Record this one as a DUPLICATE instead.
        db.rollback()
        decision = Decision("rejected", [DUPLICATE], decision.hash, decision.new_last_seq)
        meter = db.exec(select(Meter).where(Meter.meter_id == reading["meterId"])).first()
        meter.last_seq = max(meter.last_seq, decision.new_last_seq)
        db.add(meter)
        row = save_reading(db, reading, decision)
        db.commit()
    db.refresh(row)

    # The decision is safely stored; now the slow part: Hedera.
    hedera_result = anchor_and_reward(db, row)

    return JSONResponse({
        "readingId": row.id,
        "meterId": row.meter_id,
        "seq": row.seq,
        "decision": decision.decision,
        "reasons": decision.reasons,
        "hash": decision.hash,
        **hedera_result,
    })


# --- Marketplace (Phase 6) ----------------------------------------------------

VKWH_DECIMALS = 6        # 1 unit = 1 mWh
HBAR_DECIMALS = 8        # 1 unit = 1 tinybar
OPEN, LOCKED, FILLED, CANCELLED = "open", "locked", "filled", "cancelled"
MAX_HEDERA_UNITS = 2**63 - 1   # Hedera amounts are signed 64-bit integers


def parse_units(text: str, decimals: int) -> int | None:
    """'0.5' with 6 decimals -> 500000. None if not a positive number with at
    most `decimals` decimal places. Uses Decimal, so no float rounding."""
    try:
        value = Decimal(text.strip())
    except (InvalidOperation, AttributeError):
        return None
    if not value.is_finite() or value <= 0:
        return None
    scaled = value * (10 ** decimals)
    if scaled != scaled.to_integral_value() or scaled > MAX_HEDERA_UNITS:
        return None
    return int(scaled)


def price_per_kwh(offer: Offer) -> str:
    """HBAR per VKWH (= per kWh), for comparing offers."""
    per = Decimal(offer.price_tinybar) / Decimal(10**8) / (Decimal(offer.amount_mwh) / Decimal(10**6))
    return f"{per:,.2f}"


templates.env.filters["price_per_kwh"] = price_per_kwh


def flash(request: Request, kind: str, text: str, link: str | None = None) -> None:
    """A one-time message shown on the next page load (success or error)."""
    request.session["flash"] = {"kind": kind, "text": text, "link": link}


def listed_mwh(db: Session, user: User) -> int:
    """VKWH the user has already put up for sale (open or mid-purchase)."""
    return db.exec(
        select(func.coalesce(func.sum(Offer.amount_mwh), 0))
        .where(Offer.seller_id == user.id, Offer.status.in_([OPEN, LOCKED]))
    ).one()


def trade_message(offer: Offer, seller: User, buyer: User) -> str:
    """The `trades` HCS message (Plan.md schema), compact JSON, under 1 KB."""
    return json.dumps(
        {"t": "trade", "v": 1, "offerId": offer.id, "seller": seller.account_id, "buyer": buyer.account_id,
         "vkwh": format_vkwh(offer.amount_mwh), "hbar": format_hbar(offer.price_tinybar), "tx": offer.tx_id},
        separators=(",", ":"),
    )


def friendly_swap_error(message: str) -> str:
    if "INSUFFICIENT_ACCOUNT_BALANCE" in message or "INSUFFICIENT_PAYER_BALANCE" in message:
        return "You don't have enough HBAR for this offer."
    if "INSUFFICIENT_TOKEN_BALANCE" in message:
        return "The seller no longer has enough VKWH, so the offer was cancelled."
    return f"The trade didn't go through, and nothing moved. {message}"


def market_context(db: Session, user: User) -> dict:
    users = {u.id: u for u in db.exec(select(User))}
    open_offers = db.exec(select(Offer).where(Offer.status == OPEN).order_by(Offer.id.desc())).all()
    my_offers = db.exec(
        select(Offer).where(Offer.seller_id == user.id, Offer.status.in_([OPEN, LOCKED])).order_by(Offer.id.desc())
    ).all()
    trades = db.exec(select(Offer).where(Offer.status == FILLED).order_by(Offer.updated_at.desc()).limit(20)).all()
    balances = hedera.get_balances(user.account_id)
    listed = listed_mwh(db, user)
    available = max(balances["vkwh_mwh"] - listed, 0) if balances else None
    return {
        "user": user, "users": users, "balances": balances, "listed_mwh": listed, "available_mwh": available,
        "offers": [o for o in open_offers if o.seller_id != user.id],
        "my_offers": my_offers, "trades": trades,
    }


@app.get("/market", response_class=HTMLResponse)
def market_page(request: Request, db: DbSession, user: CurrentUser):
    if not user:
        return RedirectResponse("/login", status_code=303)
    context = market_context(db, user)
    context["flash"] = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "market.html", context)


@app.post("/offers")
def create_offer(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    amount_vkwh: Annotated[str, Form()],
    price_hbar: Annotated[str, Form()],
):
    """List VKWH for sale at a fixed HBAR price."""
    if not user:
        return RedirectResponse("/login", status_code=303)
    amount = parse_units(amount_vkwh, VKWH_DECIMALS)
    price = parse_units(price_hbar, HBAR_DECIMALS)
    if amount is None:
        flash(request, "error", "Amount must be a positive number of VKWH with at most 6 decimals (e.g. 0.5).")
    elif price is None:
        flash(request, "error", "Price must be a positive number of HBAR with at most 8 decimals (e.g. 2).")
    else:
        balances = hedera.get_balances(user.account_id)
        if balances is None:
            flash(request, "error", "Couldn't check your VKWH balance on Hedera right now. Try again in a moment.")
        elif amount > balances["vkwh_mwh"] - listed_mwh(db, user):
            available = max(balances["vkwh_mwh"] - listed_mwh(db, user), 0)
            flash(request, "error", f"You can list at most {format_vkwh(available)} VKWH "
                                    "(your balance minus what you've already listed).")
        else:
            db.add(Offer(seller_id=user.id, amount_mwh=amount, price_tinybar=price))
            db.commit()
            flash(request, "success", f"Listed {format_vkwh(amount)} VKWH for {format_hbar(price)} HBAR.")
    return RedirectResponse("/market", status_code=303)


@app.post("/offers/{offer_id}/buy")
def buy_offer(request: Request, db: DbSession, user: CurrentUser, offer_id: int):
    """Buy an offer: ONE atomic Hedera transfer moves VKWH seller->buyer and
    HBAR buyer->seller, signed by both custodial keys (rule 9)."""
    if not user:
        return RedirectResponse("/login", status_code=303)
    offer = db.get(Offer, offer_id)
    if offer is None:
        flash(request, "error", "That offer doesn't exist.")
        return RedirectResponse("/market", status_code=303)
    if offer.seller_id == user.id:
        flash(request, "error", "You can't buy your own offer.")
        return RedirectResponse("/market", status_code=303)

    # Lock: only one buyer can move the offer from open to locked.
    locked = db.execute(
        update(Offer).where(Offer.id == offer_id, Offer.status == OPEN)
        .values(status=LOCKED, buyer_id=user.id, updated_at=utc_now())
    )
    db.commit()
    if locked.rowcount != 1:
        flash(request, "error", "This offer is no longer available. Someone else bought it, or it was cancelled.")
        return RedirectResponse("/market", status_code=303)
    db.refresh(offer)
    seller = db.get(User, offer.seller_id)

    def release(status: str) -> None:
        offer.status = status
        offer.buyer_id = None
        offer.updated_at = utc_now()
        db.add(offer)
        db.commit()

    balances = hedera.get_balances(user.account_id)
    if balances is not None and balances["hbar_tinybar"] < offer.price_tinybar:
        release(OPEN)
        flash(request, "error", "You don't have enough HBAR for this offer.")
        return RedirectResponse("/market", status_code=303)

    try:
        tx_id = hedera.atomic_swap(
            hedera.config_id("VKWH_TOKEN_ID"),
            seller.account_id, decrypt_key(seller.encrypted_key),
            user.account_id, decrypt_key(user.encrypted_key),
            offer.amount_mwh, offer.price_tinybar,
        )
    except hedera.HederaError as exc:
        message = str(exc)
        release(CANCELLED if "INSUFFICIENT_TOKEN_BALANCE" in message else OPEN)
        flash(request, "error", friendly_swap_error(message))
        return RedirectResponse("/market", status_code=303)

    offer.status = FILLED
    offer.tx_id = tx_id
    offer.updated_at = utc_now()
    db.add(offer)
    db.commit()
    try:
        hedera.submit_message(hedera.config_id("TRADES_TOPIC_ID"), trade_message(offer, seller, user))
    except hedera.HederaError:
        pass   # the trade itself is final on Hedera; the log message is best effort
    flash(request, "success",
          f"Bought {format_vkwh(offer.amount_mwh)} VKWH for {format_hbar(offer.price_tinybar)} HBAR "
          "in ONE Hedera transaction: both transfers succeeded together.",
          hedera.hashscan_tx(tx_id))
    return RedirectResponse("/market", status_code=303)


@app.post("/offers/{offer_id}/cancel")
def cancel_offer(request: Request, db: DbSession, user: CurrentUser, offer_id: int):
    if not user:
        return RedirectResponse("/login", status_code=303)
    cancelled = db.execute(
        update(Offer).where(Offer.id == offer_id, Offer.seller_id == user.id, Offer.status == OPEN)
        .values(status=CANCELLED, updated_at=utc_now())
    )
    db.commit()
    if cancelled.rowcount == 1:
        flash(request, "success", "Offer cancelled.")
    else:
        flash(request, "error", "You can only cancel your own open offers.")
    return RedirectResponse("/market", status_code=303)
