"""Import preview/commit payloads (§4, §8, GL-2-8, GL-3.5-5).

Row indices are 1-based over data rows only; the header row is not numbered.
Sample values are typed for JSON/YAML sources (numbers, booleans, arrays,
objects); CSV/XLSX values are the raw cell strings.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ImportSampleRow(BaseModel):
    row: int
    values: dict[str, Any]


class ImportValidationError(BaseModel):
    row: int
    column: str
    reason: str


class ImportValidationReport(BaseModel):
    valid: int
    errors: list[ImportValidationError]


class ImportPreviewResponse(BaseModel):
    columns: list[str]
    suggested_mapping: dict[str, str]
    sample_rows: list[ImportSampleRow]
    total_rows: int
    validation: ImportValidationReport | None = None
    # JSON/YAML only: the key the records were read from, and the other
    # top-level keys that were ignored.
    records_key: str | None = None
    ignored_keys: list[str] = []


class ImportCommitResponse(BaseModel):
    imported: int
    skipped: int
    errors: list[ImportValidationError]
