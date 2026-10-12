"""Passwords, custodial key encryption, and the logged-in user.

- Passwords are hashed with argon2 (one-way; the password itself is never stored).
- User Hedera keys are encrypted with Fernet, keyed by KEY_ENCRYPTION_SECRET.
- The logged-in user's ID lives in the signed session cookie (SessionMiddleware).
"""

import os

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from fastapi import Depends, Request
from sqlmodel import Session

from app.models import User, get_session

load_dotenv()

_hasher = PasswordHasher()


# --- Passwords --------------------------------------------------------------

def normalize_email(email: str) -> str:
    """Emails are compared and stored lowercase, without surrounding spaces."""
    return email.strip().lower()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """True if the password matches the stored hash. Never raises."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


# --- Custodial key encryption -----------------------------------------------

def _fernet() -> Fernet:
    """Build the Fernet cipher from .env. Read on each call so tests can set it."""
    secret = os.getenv("KEY_ENCRYPTION_SECRET", "").strip()
    try:
        return Fernet(secret)
    except ValueError:
        # Don't echo the secret in the error.
        raise RuntimeError(
            "KEY_ENCRYPTION_SECRET in .env is missing or not a valid Fernet key."
        ) from None


def encrypt_key(private_key: str) -> str:
    """Encrypt a user's private key (DER hex) for storage. Returns a 'gAAAA...' token."""
    return _fernet().encrypt(private_key.encode()).decode()


def decrypt_key(token: str) -> str:
    """Decrypt a stored key. Only call this right before signing a transaction."""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        raise RuntimeError(
            "A stored key could not be decrypted. Was KEY_ENCRYPTION_SECRET changed?"
        ) from None


# --- Session ----------------------------------------------------------------

def log_in(request: Request, user: User) -> None:
    request.session.clear()
    request.session["user_id"] = user.id


def log_out(request: Request) -> None:
    request.session.clear()


def current_user(request: Request, session: Session = Depends(get_session)) -> User | None:
    """FastAPI dependency: the logged-in User, or None if nobody is logged in."""
    user_id = request.session.get("user_id")
    if user_id is None:
        return None
    return session.get(User, user_id)
