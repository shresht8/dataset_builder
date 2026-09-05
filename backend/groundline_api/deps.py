"""Shared FastAPI dependencies: DB session and current-user resolution.

`get_current_user` accepts either an OIDC session (interactive UI) or a personal
access token (machine access, §5) and yields the resolved user with role.
"""

from __future__ import annotations

from collections.abc import Iterator

from groundline_api.db import SessionLocal


def get_db() -> Iterator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user():
    """Resolve the caller from an OIDC session or a PAT. TODO."""
    raise NotImplementedError
