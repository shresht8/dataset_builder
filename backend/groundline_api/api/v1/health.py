"""Health check (§8).

    GET /health    200 {"status": "ok"} when a `select 1` succeeds, else 503
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from groundline_api.deps import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: Annotated[Session, Depends(get_db)]) -> dict[str, str]:
    try:
        db.execute(text("select 1"))
    except OperationalError:
        raise HTTPException(status_code=503, detail="database unreachable")
    return {"status": "ok"}
