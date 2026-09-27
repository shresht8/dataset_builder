"""Import preview/commit payloads (§4, §8, GL-2-8).

Row indices are 1-based over data rows only; the header row is not numbered.
"""

from __future__ import annotations

from pydantic import BaseModel


class ImportSampleRow(BaseModel):
    row: int
    values: dict[str, str]


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


class ImportCommitResponse(BaseModel):
    imported: int
    skipped: int
    errors: list[ImportValidationError]
