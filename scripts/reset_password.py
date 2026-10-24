"""Set a new password for an account (admin tool for your own database).

Passwords can't be recovered: only an argon2 hash is stored. This replaces the
hash with one for a new password, typed into a hidden prompt (never echoed or
logged). The user's Hedera account, keys and VKWH are untouched.

It uses DATABASE_URL from .env, i.e. your local verdet.db unless you point it
elsewhere.

Run: .venv\\Scripts\\python.exe scripts\\reset_password.py you@example.com
"""

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so "app" imports

from sqlmodel import Session, select  # noqa: E402

from app.auth import hash_password, normalize_email  # noqa: E402
from app.main import MIN_PASSWORD_LENGTH  # noqa: E402
from app.models import DATABASE_URL, User, engine  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    email = normalize_email(sys.argv[1])
    where = "local verdet.db" if DATABASE_URL.startswith("sqlite") else "the database in DATABASE_URL"
    with Session(engine) as session:
        user = session.exec(select(User).where(User.email == email)).first()
        if user is None:
            print(f"No account with email {email} in {where}.")
            return 1
        print(f"Resetting the password for {email} (App ID {user.account_id}) in {where}.")
        new = getpass.getpass("New password (hidden): ")
        if len(new) < MIN_PASSWORD_LENGTH:
            print(f"Too short: at least {MIN_PASSWORD_LENGTH} characters. Nothing changed.")
            return 1
        if getpass.getpass("Type it again: ") != new:
            print("The two entries don't match. Nothing changed.")
            return 1
        user.password_hash = hash_password(new)
        session.add(user)
        session.commit()
    print("Done. Log in with the new password.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
