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


class ImportColumnProblem(BaseModel):
    """Rows failing in one column (column "" = the record as a whole) and an example reason."""

    column: str
    rows: int
    reason: str


class ImportValidationReport(BaseModel):
    valid: int
    # At most ERROR_ROWS_SHOWN rows' worth; the fields below are exact totals.
    errors: list[ImportValidationError]
    error_total: int
    failed_rows: int
    checked_rows: int  # total rows minus blank lines
    by_column: list[ImportColumnProblem]


class ImportCompatibility(BaseModel):
    """How a mapping fits the schema (GL-133); keys are source / schema column keys."""

    matched: list[str]
    ignored_source_columns: list[str]
    unfilled_schema_columns: list[str]
    required_without_source: list[str]


class ImportPreviewResponse(BaseModel):
    columns: list[str]
    suggested_mapping: dict[str, str]
    sample_rows: list[ImportSampleRow]
    total_rows: int  # includes blank CSV/XLSX lines, so indices stay file positions
    blank_skipped: int = 0
    # Against the supplied mapping, or the suggested one when none was supplied.
    compatibility: ImportCompatibility
    validation: ImportValidationReport | None = None
    # JSON/YAML only: the key the records were read from, and the other
    # top-level keys that were ignored.
    records_key: str | None = None
    ignored_keys: list[str] = []


class ImportCommitResponse(BaseModel):
    imported: int
    skipped: int
    errors: list[ImportValidationError]
    blank_skipped: int = 0
