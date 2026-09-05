"""Row routes (§8). Optimistic locking via If-Match on PATCH (§4).

    GET   /datasets/{id}/rows?status=&assignee=&q=
    POST  /datasets/{id}/rows
    PATCH /datasets/{id}/rows/{rid}   If-Match: <rev>
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/datasets", tags=["rows"])

# TODO: list_rows, create_row, patch_row (409 on rev mismatch)
