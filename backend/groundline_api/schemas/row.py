"""Row create/read/patch payloads (§8).

PATCH carries the expected `rev` via If-Match; a mismatch returns 409 (§4).
"""

from __future__ import annotations

# TODO: RowCreate, RowRead, RowPatch, RowsFromTraces
