"""VERDET web app: routes and pages.

Run: uvicorn app.main:app --reload   (then open http://127.0.0.1:8000)
Routes are plain `def` because the Hedera SDK is synchronous; FastAPI runs
them in a thread pool so a slow Hedera call doesn't block other requests.
"""

import json
import os
import secrets
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Body, Depends, FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
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
from app.models import Meter, Offer, Reading, Redemption, User, create_db, get_session, utc_now
from app.verify import DUPLICATE, Decision, signature_valid, verify_reading
from gateway.canonical import METER_ID_PATTERN, canonical_string, reading_hash

load_dotenv()

MIN_PASSWORD_LENGTH = 8
MAX_RATED_W = 10_000          # prototype: small DC panels only
READINGS_SHOWN = 50           # rows in the readings table
LAGOS = timezone(timedelta(hours=1))   # WAT, no daylight saving
DEFAULT_LAT, DEFAULT_LON = 6.52, 3.38  # meters are sited in Lagos (no location field in the form)
ONLINE_SECONDS = 180          # a meter is "online" if it reported in the last 3 minutes
NEW_ROW_SECONDS = 6           # readings younger than this flash in the table
# Impact estimates (shown with their formula on /impact; override in .env).
DIESEL_L_PER_KWH = float(os.getenv("DIESEL_L_PER_KWH", "0.3"))   # small diesel generator, typical
CO2_KG_PER_L = float(os.getenv("CO2_KG_PER_L", "2.68"))          # CO2 from burning 1 L of diesel


def _session_secret() -> str:
    secret = os.getenv("SESSION_SECRET", "").strip()
    if not secret or secret == "change-me":
        raise RuntimeError("Set SESSION_SECRET in .env (see .env.example for how to generate one).")
    return secret


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db()   # create any missing tables at startup
    yield


ON_RENDER = bool(os.getenv("RENDER"))   # Render sets this; the live site is HTTPS-only

app = FastAPI(title="VERDET", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=_session_secret(), same_site="lax", https_only=ON_RENDER)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


@app.get("/healthz")
def healthz(db: Annotated[Session, Depends(get_session)]) -> JSONResponse:
    """For Render's health check: the app is up and the database answers."""
    db.exec(select(func.count(User.id))).one()
    return JSONResponse({"ok": True})


@app.get("/sw.js")
def service_worker() -> FileResponse:
    """Served from the root so the PWA's service worker controls the whole site."""
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})


@app.get("/offline", response_class=HTMLResponse)
def offline(request: Request):
    """Shown by the service worker when the phone has no connection."""
    return templates.TemplateResponse(request, "offline.html", {"user": None})


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


def format_energy(mwh: int) -> str:
    """Pick a readable unit: 167 mWh, 1.24 Wh, 1.235 kWh."""
    if mwh < 1_000:
        return f"{mwh} mWh"
    if mwh < 1_000_000:
        return f"{mwh / 1_000:,.2f} Wh"
    return f"{mwh / 1_000_000:,.3f} kWh"


def as_utc(moment: datetime) -> datetime:
    """Timezone-aware UTC. SQLite hands back naive UTC times; queries need aware ones."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def failed_step(reasons: str) -> int | None:
    """Which verification step (0-3) a rejection failed at; None if accepted."""
    steps = {"UNREGISTERED": 0, "BAD_SIGNATURE": 1, "REPLAY": 2, "DUPLICATE": 2,
             "PHYSICS_LIMIT": 3}
    for code in reasons.split(","):
        if code in steps:
            return steps[code]
    return None


templates.env.filters["vkwh"] = format_vkwh
templates.env.filters["hbar"] = format_hbar
templates.env.filters["lagos_time"] = format_lagos_time
templates.env.filters["energy"] = format_energy
templates.env.filters["failed_step"] = failed_step
templates.env.globals["hashscan_account"] = hedera.hashscan_account
templates.env.globals["hashscan_tx"] = hedera.hashscan_tx
templates.env.globals["hashscan_topic"] = hedera.hashscan_topic
templates.env.globals["hashscan_token"] = hedera.hashscan_token
templates.env.globals["config_value"] = lambda name: os.getenv(name, "")

CurrentUser = Annotated[User | None, Depends(current_user)]
DbSession = Annotated[Session, Depends(get_session)]


# --- Pages ------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def landing(request: Request, db: DbSession, user: CurrentUser):
    """Public landing page. Logged-in users go straight to their dashboard."""
    if user:
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse(request, "landing.html", {"user": None, **impact_stats(db)})


@app.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request, user: CurrentUser):
    if user:
        return RedirectResponse("/dashboard", status_code=303)
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
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, user: CurrentUser):
    if user:
        return RedirectResponse("/dashboard", status_code=303)
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
    return RedirectResponse("/dashboard", status_code=303)


@app.post("/logout")
def logout(request: Request) -> RedirectResponse:
    log_out(request)
    return RedirectResponse("/login", status_code=303)


@app.get("/wallet")
def wallet() -> RedirectResponse:
    """Old URL: the wallet card now lives on the dashboard."""
    return RedirectResponse("/dashboard", status_code=303)


def user_meter(db: Session, user: User) -> Meter | None:
    """Each account has at most one meter."""
    return db.exec(select(Meter).where(Meter.owner_id == user.id)).first()


def meter_status(db: Session, meter: Meter | None) -> dict:
    """Online if the meter reported recently; when it was last seen."""
    if meter is None:
        return {"state": "none", "last_seen": None}
    last = db.exec(select(Reading).where(Reading.meter_id == meter.meter_id).order_by(Reading.id.desc())).first()
    if last is None:
        return {"state": "waiting", "last_seen": None}
    age = datetime.now(timezone.utc) - as_utc(last.created_at)
    return {"state": "online" if age.total_seconds() <= ONLINE_SECONDS else "offline", "last_seen": last.created_at}


def dashboard_context(db: Session, user: User) -> dict:
    meter = user_meter(db, user)
    midnight = datetime.now(LAGOS).replace(hour=0, minute=0, second=0, microsecond=0)
    today = {"accepted": 0, "rejected": 0, "mwh": 0}
    rewarded_mwh = 0
    recent: list[Reading] = []
    if meter:
        rows = db.exec(select(Reading).where(Reading.meter_id == meter.meter_id,
                                             Reading.created_at >= as_utc(midnight))).all()
        for r in rows:
            today[r.decision] = today.get(r.decision, 0) + 1
            if r.decision == "accepted":
                today["mwh"] += r.mwh
        rewarded_mwh = db.exec(
            select(func.coalesce(func.sum(Reading.mwh), 0))
            .where(Reading.meter_id == meter.meter_id, Reading.mint_tx.is_not(None))
        ).one()
        recent = db.exec(select(Reading).where(Reading.meter_id == meter.meter_id)
                         .order_by(Reading.id.desc()).limit(5)).all()
    return {"user": user, "meter": meter, "status": meter_status(db, meter), "today": today,
            "rewarded_mwh": rewarded_mwh, "recent": recent,
            "checklist": onboarding_checklist(db, user, meter, bool(recent), rewarded_mwh)}


def onboarding_checklist(db: Session, user: User, meter: Meter | None, has_reading: bool, rewarded_mwh: int) -> list[dict]:
    """Getting-started steps, each ticked from real data. The card hides when all are done."""
    traded = db.exec(select(func.count(Offer.id)).where(
        Offer.status == "filled", (Offer.seller_id == user.id) | (Offer.buyer_id == user.id))).one() > 0
    return [
        {"title": "Create your account", "text": "Your App ID is a real Hedera account.", "done": True, "href": None},
        {"title": "Add your meter", "text": "Register the device's public key.", "done": meter is not None, "href": "/meters"},
        {"title": "Send a first reading", "text": "Start the bridge or the simulator.", "done": has_reading, "href": "/readings"},
        {"title": "Earn your first VKWH", "text": "Accepted energy is minted to you.", "done": rewarded_mwh > 0, "href": "/readings"},
        {"title": "Make a first trade", "text": "Sell VKWH or buy someone else's.", "done": traded, "href": "/market"},
    ]


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: DbSession, user: CurrentUser):
    if not user:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "dashboard.html", dashboard_context(db, user))


@app.get("/api/me/power")
def my_power(db: DbSession, user: CurrentUser) -> JSONResponse:
    """Average power of the last 40 accepted readings from the user's meter, for the live chart.
    Rejected readings aren't trustworthy measurements, so they aren't plotted."""
    if not user:
        return JSONResponse({"error": "login required"}, status_code=401)
    meter = user_meter(db, user)
    if meter is None:
        return JSONResponse({"points": []})
    rows = db.exec(select(Reading).where(Reading.meter_id == meter.meter_id, Reading.decision == "accepted")
                   .order_by(Reading.id.desc()).limit(40)).all()
    points = []
    for r in reversed(rows):
        window_ms = max(r.t_end_ms - r.t_start_ms, 1)
        avg_w = r.mwh * 3_600_000 / window_ms / 1000      # mWh over the window -> average watts
        points.append({"t": format_lagos_time(r.created_at)[-8:-3], "w": round(avg_w, 2),
                       "ok": r.decision == "accepted"})
    return JSONResponse({"points": points})


@app.get("/wallet/balances", response_class=HTMLResponse)
def wallet_balances(request: Request, user: CurrentUser):
    """HTMX partial: the balance lines inside the wallet card."""
    if not user:
        return Response(status_code=401)
    balances = hedera.get_balances(user.account_id)
    return templates.TemplateResponse(request, "_balances.html", {"balances": balances})


# --- Readings page ------------------------------------------------------------

READING_FILTERS = ("all", "accepted", "rejected")


def user_readings_context(db: Session, user: User, show: str = "all") -> dict:
    """The latest readings from this user's meters, plus accepted totals."""
    show = show if show in READING_FILTERS else "all"
    meter_ids = list(db.exec(select(Meter.meter_id).where(Meter.owner_id == user.id)))
    if not meter_ids:
        return {"readings": [], "accepted_mwh": 0, "accepted_count": 0, "has_meters": False, "show": show}
    query = select(Reading).where(Reading.meter_id.in_(meter_ids))
    if show != "all":
        query = query.where(Reading.decision == show)
    readings = db.exec(query.order_by(Reading.id.desc()).limit(READINGS_SHOWN)).all()
    fresh_after = datetime.now(timezone.utc) - timedelta(seconds=NEW_ROW_SECONDS)
    new_ids = {r.id for r in readings if as_utc(r.created_at) >= fresh_after}
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
            "rewarded_mwh": rewarded_mwh, "has_meters": True, "show": show, "new_ids": new_ids}


@app.get("/readings", response_class=HTMLResponse)
def readings_page(request: Request, user: CurrentUser, show: str = "all"):
    if not user:
        return RedirectResponse("/login", status_code=303)
    show = show if show in READING_FILTERS else "all"
    return templates.TemplateResponse(request, "readings.html", {"user": user, "show": show})


@app.get("/readings/table", response_class=HTMLResponse)
def readings_table(request: Request, db: DbSession, user: CurrentUser, show: str = "all"):
    """HTMX partial: the readings table, refreshed every 5 s."""
    if not user:
        return Response(status_code=401)
    return templates.TemplateResponse(request, "_readings_table.html", user_readings_context(db, user, show))


def owned_reading(db: Session, user: User, reading_id: int) -> tuple[Reading, Meter] | None:
    """A reading from one of this user's meters (and that meter), or None."""
    reading = db.get(Reading, reading_id)
    if reading is None:
        return None
    meter = db.exec(select(Meter).where(Meter.meter_id == reading.meter_id, Meter.owner_id == user.id)).first()
    return (reading, meter) if meter else None


def reading_fields(r: Reading) -> dict:
    """The reading as the meter sent it (camelCase), minus the signature."""
    return {"meterId": r.meter_id, "seq": r.seq, "tStartMs": r.t_start_ms, "tEndMs": r.t_end_ms,
            "mWh": r.mwh, "peakMw": r.peak_mw, "avgMv": r.avg_mv, "avgMa": r.avg_ma}


@app.get("/readings/{reading_id}", response_class=HTMLResponse)
def reading_detail(request: Request, db: DbSession, user: CurrentUser, reading_id: int):
    """Everything about one reading: what was signed, the checks, and its proof on Hedera."""
    if not user:
        return RedirectResponse("/login", status_code=303)
    found = owned_reading(db, user, reading_id)
    if found is None:
        flash(request, "error", "That reading isn't one of yours, or it doesn't exist.")
        return RedirectResponse("/readings", status_code=303)
    reading, meter = found
    fields = reading_fields(reading)
    canonical = canonical_string(fields)
    return templates.TemplateResponse(request, "reading_detail.html", {
        "user": user, "r": reading, "meter": meter, "fields": fields, "canonical": canonical,
        "hash_ok": reading_hash(canonical) == reading.hash,
        "sig_ok": signature_valid(meter.pubkey_hex, canonical, reading.sig) if reading.sig else None,
        "window_s": (reading.t_end_ms - reading.t_start_ms) / 1000,
    })


@app.get("/readings/{reading_id}/hcs", response_class=HTMLResponse)
def reading_hcs(request: Request, db: DbSession, user: CurrentUser, reading_id: int):
    """HTMX partial: the decision message as Hedera recorded it (from the mirror node)."""
    if not user:
        return Response(status_code=401)
    found = owned_reading(db, user, reading_id)
    if found is None:
        return Response(status_code=404)
    reading, _ = found
    message, parsed = None, None
    if reading.hcs_tx:
        try:
            message = hedera.get_hcs_message(hedera.config_id("READINGS_TOPIC_ID"), reading.hcs_tx)
        except hedera.HederaError:
            message = None
        if message:
            try:
                parsed = json.loads(message["text"])
            except ValueError:
                parsed = None
    return templates.TemplateResponse(request, "_hcs_message.html", {
        "r": reading, "message": message, "parsed": parsed,
        "pretty": json.dumps(parsed, indent=2) if parsed else (message or {}).get("text"),
        "hash_on_chain_ok": bool(parsed) and parsed.get("hash") == reading.hash,
    })


# --- Live events (pop-ups) ------------------------------------------------------

SETTLE_SECONDS = 30   # a reading still waiting on Hedera after this long is reported anyway
EVENT_MAX_AGE_SECONDS = 90   # older decisions don't pop up (no backlog flood after time away)


@app.get("/api/me/events")
def my_events(db: DbSession, user: CurrentUser, after: int | None = None) -> JSONResponse:
    """New, settled decisions on the user's meter since reading id `after`, for pop-ups.
    Without `after`, returns just the current cursor (so old readings don't pop up)."""
    if not user:
        return JSONResponse({"error": "login required"}, status_code=401)
    meter = user_meter(db, user)
    if meter is None:
        return JSONResponse({"cursor": after or 0, "events": []})
    if after is None:
        latest = db.exec(select(func.coalesce(func.max(Reading.id), 0)).where(Reading.meter_id == meter.meter_id)).one()
        return JSONResponse({"cursor": latest, "events": []})
    rows = db.exec(select(Reading).where(Reading.meter_id == meter.meter_id, Reading.id > after)
                   .order_by(Reading.id).limit(200)).all()
    now = datetime.now(timezone.utc)
    settled_before = now - timedelta(seconds=SETTLE_SECONDS)
    too_old = now - timedelta(seconds=EVENT_MAX_AGE_SECONDS)
    events, cursor = [], after
    for r in rows:
        created = as_utc(r.created_at)
        if not (r.hcs_tx or created < settled_before):
            break                               # still talking to Hedera: report it next time
        cursor = r.id
        if created < too_old:
            continue                            # old news: skip silently (it's on the Readings page)
        events.append({"id": r.id, "seq": r.seq, "decision": r.decision, "reasons": r.reasons,
                       "mwh": r.mwh, "vkwh": format_vkwh(r.mwh), "minted": bool(r.mint_tx)})
    return JSONResponse({"cursor": cursor, "events": events})


# --- Meters -----------------------------------------------------------------

SIGNATURE_PASSED = ("REPLAY", "DUPLICATE", "PHYSICS_LIMIT")


def parse_meter_form(meter_id: str, pubkey_hex: str, rated_w: str) -> tuple[dict, str | None]:
    """Clean up the Add-meter form. Returns (values, error message or None)."""
    values = {
        "meter_id": meter_id.strip(),
        "pubkey_hex": pubkey_hex.strip().lower(),
        "rated_w": rated_w.strip(),
    }
    if not METER_ID_PATTERN.match(values["meter_id"]):
        return values, "Meter ID must be 1-32 letters, digits or dashes (e.g. VD-0001)."
    key = values["pubkey_hex"]
    if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        return values, "Public key must be the 64 hex characters the meter prints as PUB."
    try:
        rated = int(values["rated_w"])
    except ValueError:
        return values, "Rated watts must be a whole number."
    if not 1 <= rated <= MAX_RATED_W:
        return values, f"Rated watts must be between 1 and {MAX_RATED_W}."
    values["rated_w"] = rated
    return values, None


def registry_message(meter: Meter, owner_account: str, event: str = "meter.register") -> str:
    """The `registry` HCS message (Plan.md schema), compact JSON, under 1 KB."""
    msg = {"t": event, "v": 1, "meterId": meter.meter_id, "owner": owner_account}
    if event == "meter.register":
        msg.update(pubKey=meter.pubkey_hex, ratedW=meter.rated_w, lat=meter.lat, lon=meter.lon)
    return json.dumps(msg, separators=(",", ":"))


def previous_last_seq(db: Session, meter_id: str) -> int:
    """If this meter ID was registered before, continue from its highest genuine seq,
    so old signed readings can't be replayed for a second reward after re-registering."""
    return db.exec(
        select(func.coalesce(func.max(Reading.seq), -1)).where(
            Reading.meter_id == meter_id,
            (Reading.decision == "accepted") | Reading.reasons.in_(SIGNATURE_PASSED),
        )
    ).one()


def render_meters(request: Request, db: Session, user: User, status: int = 200, **extra):
    meter = user_meter(db, user)
    context = {"user": user, "meter": meter, "status": meter_status(db, meter), "form": {}, **extra}
    if meter:
        midnight = datetime.now(LAGOS).replace(hour=0, minute=0, second=0, microsecond=0)
        context["today_mwh"] = db.exec(
            select(func.coalesce(func.sum(Reading.mwh), 0)).where(
                Reading.meter_id == meter.meter_id, Reading.decision == "accepted",
                Reading.created_at >= as_utc(midnight))
        ).one()
    context["flash"] = context.get("flash") or request.session.pop("flash", None)
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
):
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user_meter(db, user):
        return render_meters(request, db, user, 400,
                             error="You already have a meter. Disconnect it first to add another one.")
    values, error = parse_meter_form(meter_id, pubkey_hex, rated_w)
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

    meter = Meter(owner_id=user.id, lat=DEFAULT_LAT, lon=DEFAULT_LON,
                  last_seq=previous_last_seq(db, values["meter_id"]), **values)
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
    flash(request, "success", f"Meter {meter.meter_id} registered and recorded on Hedera.", hedera.hashscan_tx(tx_id))
    return RedirectResponse("/meters", status_code=303)


@app.post("/meters/disconnect")
def disconnect_meter(request: Request, db: DbSession, user: CurrentUser):
    """Remove the user's meter (recorded on the registry topic). Its readings stay
    in the history; the same meter ID can be registered again later."""
    if not user:
        return RedirectResponse("/login", status_code=303)
    meter = user_meter(db, user)
    if meter is None:
        flash(request, "error", "You don't have a meter to disconnect.")
        return RedirectResponse("/meters", status_code=303)
    try:
        tx_id = hedera.submit_message(
            hedera.config_id("REGISTRY_TOPIC_ID"), registry_message(meter, user.account_id, "meter.disconnect")
        )
    except hedera.HederaError as exc:
        flash(request, "error", f"Couldn't record the disconnect on Hedera: {exc}")
        return RedirectResponse("/meters", status_code=303)
    db.delete(meter)
    db.commit()
    flash(request, "success", f"Meter {meter.meter_id} disconnected. You can add a meter again.",
          hedera.hashscan_tx(tx_id))
    return RedirectResponse("/meters", status_code=303)


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
        sig=reading["sig"] if isinstance(reading.get("sig"), str) and len(reading["sig"]) <= 256 else None,
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
# Price band (HBAR per kWh): offers outside it are refused, like an exchange's
# price protection. Testnet defaults; set real values in .env.
PRICE_MIN_PER_KWH = Decimal(os.getenv("PRICE_MIN_HBAR_PER_KWH", "1"))
PRICE_MAX_PER_KWH = Decimal(os.getenv("PRICE_MAX_HBAR_PER_KWH", "50"))


def unit_price(amount_mwh: int, price_tinybar: int) -> Decimal:
    """HBAR per kWh (= per VKWH) for an amount and total price."""
    return (Decimal(price_tinybar) / Decimal(10**8)) / (Decimal(amount_mwh) / Decimal(10**6))


def price_in_band(amount_mwh: int, price_tinybar: int) -> bool:
    return PRICE_MIN_PER_KWH <= unit_price(amount_mwh, price_tinybar) <= PRICE_MAX_PER_KWH


MIN_OFFER_MWH = 100                          # 0.0001 VKWH = 0.1 Wh: smaller isn't worth a transaction
PRICE_REFERENCE_PER_KWH = Decimal(os.getenv("PRICE_REFERENCE_HBAR_PER_KWH", "10"))


def offer_total(amount_mwh: int, unit_tinybar_per_kwh: int) -> int:
    """Total tinybars for an amount at a price per kWh: mWh x (tinybar/kWh) / 1,000,000, rounded down."""
    return amount_mwh * unit_tinybar_per_kwh // 1_000_000


def suggested_price(last_trade: Offer | None) -> Decimal:
    """The pre-filled price per kWh: the last trade's price if it's inside the band,
    otherwise the reference price (clamped into the band)."""
    if last_trade is not None:
        last = unit_price(last_trade.amount_mwh, last_trade.price_tinybar)
        if PRICE_MIN_PER_KWH <= last <= PRICE_MAX_PER_KWH:
            return last.quantize(Decimal("0.01"))
    return min(max(PRICE_REFERENCE_PER_KWH, PRICE_MIN_PER_KWH), PRICE_MAX_PER_KWH)


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
    return f"{unit_price(offer.amount_mwh, offer.price_tinybar):,.2f}"


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
    others = [o for o in open_offers if o.seller_id != user.id]
    # Order book: cheapest HBAR per kWh first, like an exchange's ask side.
    others.sort(key=lambda o: Decimal(o.price_tinybar) / Decimal(o.amount_mwh))
    day_ago = datetime.now(timezone.utc) - timedelta(hours=24)
    volume_24h = sum(t.amount_mwh for t in trades if as_utc(t.updated_at) >= day_ago)
    # Price history for the chart: every filled trade, oldest first.
    history = db.exec(select(Offer).where(Offer.status == FILLED).order_by(Offer.updated_at)).all()
    price_history = [{"t": format_lagos_time(t.updated_at)[:-3], "p": float(unit_price(t.amount_mwh, t.price_tinybar)),
                      "v": t.amount_mwh / 1_000_000} for t in history]
    return {
        "user": user, "users": users, "balances": balances, "listed_mwh": listed, "available_mwh": available,
        "offers": others, "my_offers": my_offers, "trades": trades, "price_history": price_history,
        "band": {"min": PRICE_MIN_PER_KWH, "max": PRICE_MAX_PER_KWH},
        "suggested": suggested_price(trades[0] if trades else None), "min_offer_mwh": MIN_OFFER_MWH,
        "ticker": {"last": trades[0] if trades else None, "volume_24h_mwh": volume_24h,
                   "open_count": len(open_offers)},
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
    price_per_kwh: Annotated[str, Form()],
):
    """List VKWH for sale. The seller sets the price per kWh (like a limit price on
    an exchange); the total is computed exactly in tinybars."""
    if not user:
        return RedirectResponse("/login", status_code=303)
    amount = parse_units(amount_vkwh, VKWH_DECIMALS)
    unit_tinybar = parse_units(price_per_kwh, HBAR_DECIMALS)      # tinybars per kWh
    price = offer_total(amount, unit_tinybar) if amount and unit_tinybar else None
    if amount is None:
        flash(request, "error", "Amount must be a positive number of VKWH with at most 6 decimals (e.g. 0.0004).")
    elif amount < MIN_OFFER_MWH:
        flash(request, "error", f"The smallest offer is {format_vkwh(MIN_OFFER_MWH)} VKWH ({format_energy(MIN_OFFER_MWH)}).")
    elif unit_tinybar is None:
        flash(request, "error", "Price per kWh must be a positive number of HBAR with at most 8 decimals (e.g. 10).")
    elif not PRICE_MIN_PER_KWH <= Decimal(unit_tinybar) / Decimal(10**8) <= PRICE_MAX_PER_KWH:
        flash(request, "error", f"Price must be between {PRICE_MIN_PER_KWH} and {PRICE_MAX_PER_KWH} HBAR per kWh. "
                                f"Yours is {Decimal(unit_tinybar) / Decimal(10**8):,.2f} HBAR per kWh.")
    elif price < 1:
        flash(request, "error", "That total is less than the smallest HBAR unit. List a larger amount.")
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
            flash(request, "success", f"Listed {format_vkwh(amount)} VKWH at {format_hbar(unit_tinybar)} HBAR/kWh. "
                                      f"You'll receive {format_hbar(price)} HBAR when it sells.")
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


# --- Impact (public) ------------------------------------------------------------

def impact_stats(db: Session) -> dict:
    """Network-wide numbers for the landing and impact pages. Everything here is
    also derivable from the public HCS topics."""
    accepted_mwh, accepted_count = db.exec(
        select(func.coalesce(func.sum(Reading.mwh), 0), func.count(Reading.id)).where(Reading.decision == "accepted")
    ).one()
    rejections = dict(db.exec(
        select(Reading.reasons, func.count(Reading.id)).where(Reading.decision == "rejected").group_by(Reading.reasons)
    ).all())
    anchored = db.exec(select(func.count(Reading.id)).where(Reading.hcs_tx.is_not(None))).one()
    day_ago = datetime.now(timezone.utc) - timedelta(hours=24)
    active_meters = db.exec(
        select(func.count(func.distinct(Reading.meter_id)))
        .where(Reading.created_at >= day_ago, Reading.meter_id.in_(select(Meter.meter_id)))
    ).one()
    trades = db.exec(select(func.count(Offer.id)).where(Offer.status == FILLED)).one()
    redeemed_count, redeemed_mwh = db.exec(
        select(func.count(Redemption.id), func.coalesce(func.sum(Redemption.amount_mwh), 0))).one()
    latest = db.exec(select(Reading).where(Reading.hcs_tx.is_not(None)).order_by(Reading.id.desc()).limit(8)).all()
    kwh = accepted_mwh / 1_000_000
    diesel_l = kwh * DIESEL_L_PER_KWH
    return {
        "accepted_mwh": accepted_mwh, "accepted_count": accepted_count,
        "rejected_count": sum(rejections.values()), "rejections": rejections,
        "anchored_count": anchored, "meters_count": db.exec(select(func.count(Meter.id))).one(),
        "active_meters": active_meters, "trades_count": trades, "latest_proofs": latest,
        "redeemed_count": redeemed_count, "redeemed_mwh": redeemed_mwh,
        "diesel_l": diesel_l, "co2_kg": diesel_l * CO2_KG_PER_L,
        "diesel_factor": DIESEL_L_PER_KWH, "co2_factor": CO2_KG_PER_L,
    }


@app.get("/impact", response_class=HTMLResponse)
def impact_page(request: Request, db: DbSession, user: CurrentUser):
    """Public: no login needed."""
    return templates.TemplateResponse(request, "impact.html", {"user": user, **impact_stats(db)})


# --- Rewards: redeem VKWH for airtime / data (Phase 6b) ----------------------------

# Demo rates, far more generous than real energy value (a 10 W panel makes ~0.05 kWh a day).
REWARDS = [
    {"code": "AIRTIME_100", "kind": "Airtime", "label": "₦100 airtime", "mwh": 500},
    {"code": "AIRTIME_200", "kind": "Airtime", "label": "₦200 airtime", "mwh": 1_000},
    {"code": "AIRTIME_500", "kind": "Airtime", "label": "₦500 airtime", "mwh": 2_500},
    {"code": "DATA_100MB", "kind": "Data", "label": "100 MB data", "mwh": 400},
    {"code": "DATA_500MB", "kind": "Data", "label": "500 MB data", "mwh": 1_500},
    {"code": "DATA_1GB", "kind": "Data", "label": "1 GB data", "mwh": 2_800},
]
REWARDS_BY_CODE = {r["code"]: r for r in REWARDS}
NETWORKS = ("MTN", "Airtel", "Glo", "9mobile")


def normalize_phone(text: str) -> str | None:
    """Nigerian mobile number -> '+234XXXXXXXXXX', or None if it isn't one.
    Accepts 0803..., 234803..., +234 803 ... (spaces and dashes ignored)."""
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) == 11 and digits.startswith("0") and digits[1] in "789":
        return "+234" + digits[1:]
    if len(digits) == 13 and digits.startswith("234") and digits[3] in "789":
        return "+" + digits
    return None


def mask_phone(phone: str) -> str:
    """'+2348031234567' -> '+234******4567'. Only this masked form is ever stored."""
    return phone[:4] + "*" * (len(phone) - 8) + phone[-4:]


def voucher_code() -> str:
    """A demo voucher like 'VD-7K2Q-9MXA' (no 0/O/1/I to avoid confusion)."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    part = lambda: "".join(secrets.choice(alphabet) for _ in range(4))  # noqa: E731
    return f"VD-{part()}-{part()}"


def redemption_message(r: Redemption, account_id: str) -> str:
    """The `redemptions` HCS message. No phone number, not even masked: HCS is public forever."""
    return json.dumps(
        {"t": "redeem", "v": 1, "id": r.id, "account": account_id, "item": r.item_code,
         "vkwh": format_vkwh(r.amount_mwh), "network": r.network, "transferTx": r.transfer_tx,
         "burnTx": r.burn_tx, "delivery": "demo"},
        separators=(",", ":"),
    )


def rewards_context(db: Session, user: User) -> dict:
    balances = hedera.get_balances(user.account_id)
    listed = listed_mwh(db, user)
    available = max(balances["vkwh_mwh"] - listed, 0) if balances else None
    history = db.exec(select(Redemption).where(Redemption.user_id == user.id)
                      .order_by(Redemption.id.desc()).limit(20)).all()
    return {"user": user, "rewards": REWARDS, "networks": NETWORKS, "available_mwh": available,
            "listed_mwh": listed, "history": history,
            "redeemed_mwh": sum(r.amount_mwh for r in history)}


@app.get("/rewards", response_class=HTMLResponse)
def rewards_page(request: Request, db: DbSession, user: CurrentUser):
    if not user:
        return RedirectResponse("/login", status_code=303)
    context = rewards_context(db, user)
    context["flash"] = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "rewards.html", context)


@app.post("/rewards/redeem")
def redeem(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    item: Annotated[str, Form()],
    network: Annotated[str, Form()],
    phone: Annotated[str, Form()],
):
    """Spend VKWH on a reward: VKWH -> treasury -> burned (real, on Hedera), then a
    demo voucher. Recorded on the `redemptions` topic."""
    if not user:
        return RedirectResponse("/login", status_code=303)
    reward = REWARDS_BY_CODE.get(item)
    full_phone = normalize_phone(phone)

    def fail(message: str) -> RedirectResponse:
        flash(request, "error", message)
        return RedirectResponse("/rewards", status_code=303)

    if reward is None:
        return fail("Pick a reward from the list.")
    if network not in NETWORKS:
        return fail("Pick your mobile network.")
    if full_phone is None:
        return fail("Enter a Nigerian mobile number, e.g. 0803 123 4567.")
    try:
        token_id = hedera.config_id("VKWH_TOKEN_ID")
        topic_id = hedera.config_id("REDEMPTIONS_TOPIC_ID")
    except hedera.HederaError as exc:
        return fail(f"Rewards aren't set up yet: {exc}")
    balances = hedera.get_balances(user.account_id)
    if balances is None:
        return fail("Couldn't check your VKWH balance on Hedera right now. Try again in a moment.")
    available = balances["vkwh_mwh"] - listed_mwh(db, user)
    if reward["mwh"] > available:
        return fail(f"{reward['label']} costs {format_vkwh(reward['mwh'])} VKWH; you have "
                    f"{format_vkwh(max(available, 0))} VKWH available (balance minus what you've listed for sale).")

    # 1. The user's VKWH goes back to the treasury. If this fails, nothing moved.
    try:
        transfer_tx = hedera.return_to_treasury(token_id, user.account_id, decrypt_key(user.encrypted_key), reward["mwh"])
    except hedera.HederaError as exc:
        return fail(f"The redemption didn't go through, and nothing moved. {exc}")
    # 2. Burn it, so it can never be spent again.
    try:
        burn_tx = hedera.burn_vkwh(token_id, reward["mwh"])
    except hedera.HederaError:
        burn_tx = None   # the VKWH is in the treasury; the burn can be retried
    row = Redemption(user_id=user.id, item_code=reward["code"], item_label=reward["label"],
                     amount_mwh=reward["mwh"], network=network, phone_masked=mask_phone(full_phone),
                     voucher_code=voucher_code(), status="completed" if burn_tx else "burn_pending",
                     transfer_tx=transfer_tx, burn_tx=burn_tx)
    db.add(row)
    db.commit()
    db.refresh(row)
    # 3. Public record (best effort; the transfer and burn are already final on Hedera).
    try:
        row.hcs_tx = hedera.submit_message(topic_id, redemption_message(row, user.account_id))
        db.add(row)
        db.commit()
    except hedera.HederaError:
        pass
    flash(request, "success",
          f"Redeemed {format_vkwh(reward['mwh'])} VKWH for {reward['label']} ({network}, {row.phone_masked}). "
          f"Demo voucher {row.voucher_code}. The VKWH was returned and burned on Hedera.",
          hedera.hashscan_tx(burn_tx or transfer_tx))
    return RedirectResponse("/rewards", status_code=303)
