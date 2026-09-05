"""Create-rows-from-traces route (§4, §8). Feature-scoped datasets only.

    POST /datasets/{id}/rows:from-traces

Pre-fills input and actual output from tagged production traces and records
source_trace_id. This is where the two MVP pillars connect (§4).
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/datasets", tags=["traces"])

# TODO: rows_from_traces
