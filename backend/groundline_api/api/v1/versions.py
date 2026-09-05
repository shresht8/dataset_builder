"""Version routes (§8). Cutting is Editor-only (§5).

    GET  /datasets/{id}/versions
    POST /datasets/{id}/versions              cut
    GET  /datasets/{id}/versions/{v}?format=jsonl|json|yaml
    GET  /datasets/{id}/versions/diff?from=&to=
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/datasets", tags=["versions"])

# TODO: list_versions, cut_version, get_version (export), diff_versions
