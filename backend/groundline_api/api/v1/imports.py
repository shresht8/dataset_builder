"""Import route (§4, §8). Multipart CSV/XLSX with a column-mapping step.

    POST /datasets/{id}/import   multipart CSV/XLSX
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/datasets", tags=["import"])

# TODO: import_file (mapping + per-row validation report)
