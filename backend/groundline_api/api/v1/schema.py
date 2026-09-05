"""Column schema routes (§8). Editor-only writes (§5).

    GET /datasets/{id}/schema
    PUT /datasets/{id}/schema
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/datasets", tags=["schema"])

# TODO: get_schema, put_schema
