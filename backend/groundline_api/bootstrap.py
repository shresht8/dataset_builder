"""First-admin bootstrap (GL-1-8).

Creates or promotes an admin by email so there is someone to create everyone
else. Idempotent: an existing user is promoted to admin and re-activated; an
existing active admin is left untouched.

Usage:
    python -m groundline_api.bootstrap admin@example.com [--name "Display Name"]

Also honoured at API startup when the BOOTSTRAP_ADMIN_EMAIL setting is set.
"""

from __future__ import annotations

import argparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from groundline_api.db import SessionLocal
from groundline_api.models.user import Role, User


def ensure_admin(db: Session, email: str, display_name: str | None = None) -> User:
    """Create or promote an admin by email. Idempotent."""
    email = email.strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, display_name=display_name or email, role=Role.ADMIN)
        db.add(user)
    else:
        user.role = Role.ADMIN
        user.active = True
        if display_name:
            user.display_name = display_name
    db.commit()
    db.refresh(user)
    return user


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or promote an admin user.")
    parser.add_argument("email")
    parser.add_argument("--name", default=None, help="display name (defaults to email)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        user = ensure_admin(db, args.email, args.name)
    finally:
        db.close()
    print(f"admin ready: {user.email} (id={user.id}, role={user.role.value})")


if __name__ == "__main__":
    main()
