"""VERDET web app: routes and pages.

Run: uvicorn app.main:app --reload   (then open http://127.0.0.1:8000)
Routes are plain `def` because the Hedera SDK is synchronous; FastAPI runs
them in a thread pool so a slow Hedera call doesn't block other requests.
"""

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Body, Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select
from starlette.middleware.sessions import SessionMiddleware

from app import hedera
from app.auth import (
    current_user,
    encrypt_key,
    hash_password,
    log_in,
    log_out,
    normalize_email,
    verify_password,
)
from app.models import Meter, Reading, User, create_db, get_session
from app.verify import DUPLICATE, Decision, verify_reading
from gateway.canonical import METER_ID_PATTERN, canonical_string

load_dotenv()

MIN_PASSWORD_LENGTH = 8
MAX_RATED_W = 10_000          # prototype: small DC panels only


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


templates.env.filters["vkwh"] = format_vkwh
templates.env.filters["hbar"] = format_hbar
templates.env.globals["hashscan_account"] = hedera.hashscan_account

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
        tx_id = hedera.submit_message(hedera.topic_id("REGISTRY_TOPIC_ID"), registry_message(meter, user.account_id))
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

    return JSONResponse({
        "readingId": row.id,
        "meterId": row.meter_id,
        "seq": row.seq,
        "decision": decision.decision,
        "reasons": decision.reasons,
        "hash": decision.hash,
    })
