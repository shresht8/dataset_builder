"""CSV / XLSX import with column mapping and per-row validation (§4).

Parses the upload, applies the caller's column mapping onto the schema,
validates each row against its column types, and returns rows that failed
with reasons so they can be fixed inline or skipped.
"""

from __future__ import annotations

# TODO: parse_upload(file) ; validate_rows(rows, columns, mapping) -> report
