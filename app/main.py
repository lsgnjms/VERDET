"""VERDET web app: routes and pages.

Run: uvicorn app.main:app --reload   (then open http://127.0.0.1:8000)
Routes are plain `def` because the Hedera SDK is synchronous; FastAPI runs
them in a thread pool so a slow Hedera call doesn't block other requests.
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
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
from app.models import User, create_db, get_session

load_dotenv()

MIN_PASSWORD_LENGTH = 8


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
