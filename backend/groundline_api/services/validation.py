"""Row data validation against the dataset column schema (§3, §8).

Shared by row create/patch (GL-2-1, `api/v1/rows.py`) and CSV/XLSX import
(GL-2-8, `services/importer.py`) so type and required-field checks live in
exactly one place.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from groundline_schema import ColumnType

from groundline_api.models.dataset import DatasetColumn


def is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    if isinstance(value, list) and len(value) == 0:
        return True
    return False


def type_error(col: DatasetColumn, value: Any) -> str | None:
    """Return an error fragment if `value` doesn't match `col.type`, else None."""
    if col.type in (ColumnType.TEXT, ColumnType.LONG_TEXT):
        if not isinstance(value, str):
            return f"expected text, got {type(value).__name__}"
    elif col.type == ColumnType.NUMBER:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"expected a number, got {type(value).__name__}"
    elif col.type == ColumnType.BOOLEAN:
        if not isinstance(value, bool):
            return f"expected a boolean, got {type(value).__name__}"
    elif col.type == ColumnType.SELECT:
        if not isinstance(value, str):
            return f"expected one of {col.options}, got {type(value).__name__}"
        if value not in (col.options or []):
            return f"'{value}' is not one of {col.options}"
    elif col.type == ColumnType.MULTI_SELECT:
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return f"expected a list of strings from {col.options}"
        bad = [v for v in value if v not in (col.options or [])]
        if bad:
            return f"{bad} not in {col.options}"
    return None


def row_errors(data: dict[str, Any], columns: list[DatasetColumn]) -> list[tuple[str, str]]:
    """Return every (column_key, reason) validation failure in `data`.

    Archived columns are accepted read-only: their values are neither
    required nor type-checked. Fields with no matching column pass through
    unvalidated (a dataset may have no schema defined yet).
    """
    errors: list[tuple[str, str]] = []
    for col in columns:
        if col.archived:
            continue
        value = data.get(col.key)
        if is_empty(value):
            if col.required:
                errors.append((col.key, "required"))
            continue
        error = type_error(col, value)
        if error:
            errors.append((col.key, error))
    return errors


def validate_row_data(data: dict[str, Any], columns: list[DatasetColumn]) -> None:
    """Raise a 422 with the first validation failure found, else return None."""
    errors = row_errors(data, columns)
    if errors:
        key, reason = errors[0]
        raise HTTPException(status_code=422, detail=f"column '{key}': {reason}")
