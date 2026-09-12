"""CSV / XLSX import: parsing, column mapping, and validation (§4, §8, GL-2-8).

Two-step flow used by `api/v1/imports.py`:
  - `preview()` -- a single pass over the file that returns a sample of the
    first rows, the total row count, and (if a mapping is supplied) the full
    per-row validation report. Persists nothing.
  - `commit()` -- a single pass that applies the mapping plus caller-supplied
    skip/fix overrides and returns, per source row, whether it imported,
    was explicitly skipped, or errored. The route creates a `DatasetRow` for
    each row `commit()` returns as valid.

Neither pass materialises the whole file: `parse_upload` returns a row
iterator (the CSV reader / openpyxl `iter_rows` generator), consumed once.

Row index convention: 1-based, counting only rows of data. The header row
itself is not numbered (the first row of data is row 1).

Coercion from spreadsheet strings, applied per mapped column before
validation:
  - number:        "4" / "4.5" -> int / float; anything else that doesn't
                    parse is left as the raw string, so validation reports
                    "expected a number".
  - boolean:        true/false/1/0/yes/no (case-insensitive, trimmed) -> bool;
                    anything else is left as the raw string.
  - multi_select:   split on comma or semicolon, each part trimmed, empty
                    parts dropped.
  - select:         trimmed string (still option-checked downstream).
  - text/long_text: the raw string, unmodified.
An empty cell is treated as an absent value (the column's `required` check
applies to it; it is never coerced).
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import openpyxl
from groundline_schema import ColumnType

from groundline_api.models.dataset import DatasetColumn
from groundline_api.services.validation import row_errors

SAMPLE_SIZE = 20


class ImportRequestError(Exception):
    """A caller-facing (422) error in the uploaded file or the supplied mapping."""


@dataclass
class ParsedSource:
    header: list[str]
    rows: Iterator[list[str]]  # raw cell strings, one list per data row


def parse_upload(filename: str, content: bytes) -> ParsedSource:
    """Parse a CSV or XLSX upload into a header row and a row iterator."""
    lower = filename.lower()
    if lower.endswith(".csv"):
        return _parse_csv(content)
    if lower.endswith(".xlsx"):
        return _parse_xlsx(content)
    raise ImportRequestError(f"unsupported file type: {filename!r} (expected .csv or .xlsx)")


def _parse_csv(content: bytes) -> ParsedSource:
    text = content.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        header = []

    def rows() -> Iterator[list[str]]:
        yield from reader

    return ParsedSource(header=header, rows=rows())


def _parse_xlsx(content: bytes) -> ParsedSource:
    workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    sheet = workbook.worksheets[0]
    row_iter = sheet.iter_rows(values_only=True)
    try:
        header = ["" if cell is None else str(cell) for cell in next(row_iter)]
    except StopIteration:
        header = []

    def rows() -> Iterator[list[str]]:
        for raw_row in row_iter:
            yield ["" if cell is None else str(cell) for cell in raw_row]

    return ParsedSource(header=header, rows=rows())


def _iterate_rows(source: ParsedSource) -> Iterator[tuple[int, dict[str, str]]]:
    """Yield (1-based row index, {source_column: raw_value}) for each data row."""
    header = source.header
    for index, raw_row in enumerate(source.rows, start=1):
        values = {
            header[i]: (raw_row[i] if i < len(raw_row) else "") for i in range(len(header))
        }
        yield index, values


def suggest_mapping(header: list[str], columns: list[DatasetColumn]) -> dict[str, str]:
    """Case-insensitive header -> schema key matches; first match wins per key."""
    keys_by_lower = {col.key.lower(): col.key for col in columns if not col.archived}
    mapping: dict[str, str] = {}
    used_keys: set[str] = set()
    for source_col in header:
        key = keys_by_lower.get(source_col.strip().lower())
        if key and key not in used_keys:
            mapping[source_col] = key
            used_keys.add(key)
    return mapping


def validate_mapping(mapping: dict[str, str], columns: list[DatasetColumn]) -> None:
    """Raise ImportRequestError for a mapping that references an unknown or duplicate key."""
    valid_keys = {col.key for col in columns}
    seen_keys: dict[str, str] = {}
    for source_col, schema_key in mapping.items():
        if schema_key not in valid_keys:
            raise ImportRequestError(f"mapping: '{schema_key}' is not a schema column")
        if schema_key in seen_keys:
            raise ImportRequestError(
                f"mapping: '{seen_keys[schema_key]}' and '{source_col}' "
                f"both map to '{schema_key}'"
            )
        seen_keys[schema_key] = source_col


def _coerce(raw: str, col_type: ColumnType) -> Any:
    if col_type == ColumnType.SELECT:
        return raw.strip()
    if col_type == ColumnType.MULTI_SELECT:
        return [part.strip() for part in re.split(r"[,;]", raw) if part.strip()]
    if col_type == ColumnType.NUMBER:
        try:
            return int(raw)
        except ValueError:
            try:
                return float(raw)
            except ValueError:
                return raw
    if col_type == ColumnType.BOOLEAN:
        lowered = raw.strip().lower()
        if lowered in ("true", "1", "yes"):
            return True
        if lowered in ("false", "0", "no"):
            return False
        return raw
    return raw  # text / long_text: as-is


def _map_row(
    raw_values: dict[str, str],
    mapping: dict[str, str],
    columns_by_key: dict[str, DatasetColumn],
) -> dict[str, Any]:
    """Apply the mapping + type coercion to one source row's raw string cells."""
    data: dict[str, Any] = {}
    for source_col, schema_key in mapping.items():
        raw = raw_values.get(source_col)
        if raw is None or raw.strip() == "":
            continue  # empty cell = absent
        data[schema_key] = _coerce(raw, columns_by_key[schema_key].type)
    return data


def preview(
    source: ParsedSource,
    columns: list[DatasetColumn],
    mapping: dict[str, str] | None,
) -> tuple[list[dict[str, Any]], int, dict[str, Any] | None]:
    """Single pass: sample rows, total count, and an optional validation report.

    Returns (sample_rows, total_rows, validation) where sample_rows is a list
    of `{"row": index, "values": {...}}` (up to SAMPLE_SIZE), and validation
    is `None` if no mapping was supplied, else `{"valid": n, "errors": [...]}`.
    """
    columns_by_key = {col.key: col for col in columns}
    sample: list[dict[str, Any]] = []
    total = 0
    valid = 0
    errors: list[dict[str, Any]] = []

    for index, raw_values in _iterate_rows(source):
        total += 1
        if len(sample) < SAMPLE_SIZE:
            sample.append({"row": index, "values": raw_values})
        if mapping is not None:
            data = _map_row(raw_values, mapping, columns_by_key)
            failures = row_errors(data, columns)
            if failures:
                errors.extend(
                    {"row": index, "column": key, "reason": reason}
                    for key, reason in failures
                )
            else:
                valid += 1

    validation = None if mapping is None else {"valid": valid, "errors": errors}
    return sample, total, validation


def commit(
    source: ParsedSource,
    columns: list[DatasetColumn],
    mapping: dict[str, str],
    skip: set[int],
    fixes: dict[int, dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, int, list[dict[str, Any]]]:
    """Single pass: apply mapping + skip/fixes, validate, and report the outcome.

    Returns (row_data_to_create, imported_count, skipped_count, errors). Every
    source row appears in exactly one of: the created rows, the skipped
    count, or the errors list (never silently dropped).
    """
    columns_by_key = {col.key: col for col in columns}
    created: list[dict[str, Any]] = []
    imported = 0
    skipped = 0
    errors: list[dict[str, Any]] = []

    for index, raw_values in _iterate_rows(source):
        if index in skip:
            skipped += 1
            continue
        data = _map_row(raw_values, mapping, columns_by_key)
        data.update(fixes.get(index, {}))
        failures = row_errors(data, columns)
        if failures:
            errors.extend(
                {"row": index, "column": key, "reason": reason} for key, reason in failures
            )
            continue
        created.append(data)
        imported += 1

    return created, imported, skipped, errors
