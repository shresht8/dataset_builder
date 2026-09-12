"""Personal access tokens for machine access (GL-1-10, §5).

Raw tokens are `glpat_` + random and are shown exactly once at issue time;
only the sha256 hash is stored. Tokens expire (default 90 days, §11 Q5), are
revocable, and inherit the issuing user's role.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from groundline_api.config import settings
from groundline_api.models.user import PersonalAccessToken, User

TOKEN_PREFIX = "glpat_"


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_token(
    db: Session, user: User, name: str, ttl_days: int | None = None
) -> tuple[str, PersonalAccessToken]:
    """Create a PAT for `user`; returns (raw_token, record). Raw is never stored."""
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    record = PersonalAccessToken(
        user_id=user.id,
        name=name,
        token_hash=_hash(raw),
        expires_at=datetime.now(timezone.utc)
        + timedelta(days=ttl_days or settings.pat_default_ttl_days),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return raw, record


def verify_token(db: Session, raw: str) -> User | None:
    """Resolve a raw token to its active issuing user; None if bad/expired/revoked."""
    if not raw.startswith(TOKEN_PREFIX):
        return None
    record = db.scalar(
        select(PersonalAccessToken).where(PersonalAccessToken.token_hash == _hash(raw))
    )
    if (
        record is None
        or record.revoked_at is not None
        or record.expires_at <= datetime.now(timezone.utc)
    ):
        return None
    return db.get(User, record.user_id)
