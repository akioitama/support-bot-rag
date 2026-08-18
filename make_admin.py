"""Promote an existing local user to admin.

Usage:
    python make_admin.py user@example.com

The user must log in once through WorkOS first so they exist in SQLite.
"""

import sys

from app.database import SessionLocal, init_db
from app.models.user import User


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python make_admin.py user@example.com")
        return 1

    email = sys.argv[1].strip()
    if not email:
        print("Usage: python make_admin.py user@example.com")
        return 1

    init_db()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if user is None:
            print(f"User not found: {email}")
            print("Log in once with that WorkOS account, then run this command again.")
            return 1

        user.role = "admin"
        db.commit()
        print(f"Success: {email} is now an admin.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
