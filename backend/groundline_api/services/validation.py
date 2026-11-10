"""Row data validation against the dataset column schema (§3, §8).

Shared by row create/patch (GL-2-1, `api/v1/rows.py`) and CSV/XLSX import
(GL-2-8, `services/importer.py`) so type and required-field checks live in
exactly one place.

`json` columns (GL-3.5-13): any JSON value; only null is empty; the value is
checked against the column's optional JSON Schema and a serialised size cap.
The key column's value must have no leading/trailing whitespace.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from fastapi import HTTPException
from groundline_schema import ColumnType
from groundline_schema.temporal import is_date
from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match
from referencing.exceptions import Unresolvable

from groundline_api.config import settings
from groundline_api.models.dataset import DatasetColumn


def key_column(columns: list[DatasetColumn]) -> DatasetColumn | None:
    """The dataset's active key column, if it has one (GL-3.5-12)."""
    return next((c for c in columns if c.is_key and not c.archived), None)


@lru_cache(maxsize=256)
def _validator(schema_json: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads(schema_json))


def _json_path(path: Any) -> str:
    """`deque([0, 'args'])` -> `[0].args`."""
    text = ""
    for part in path:
        text += f"[{part}]" if isinstance(part, int) else f".{part}"
    return text.lstrip(".")


def _json_error(col: DatasetColumn, value: Any) -> str | None:
    size = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    if size > settings.json_cell_max_bytes:
        return f"value is {size} bytes; the limit is {settings.json_cell_max_bytes}"
    if not col.json_schema:
        return None
    validator = _validator(json.dumps(col.json_schema, sort_keys=True))
    try:
        error = best_match(validator.iter_errors(value))
    except Unresolvable as exc:
        return f"json_schema has an unresolvable $ref: {exc}"
    if error is None:
        return None
    where = _json_path(error.absolute_path)
    return f"{where}: {error.message}" if where else error.message


def is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    if isinstance(value, list) and len(value) == 0:
        return True
    return False


_SHOWN_CHARS = 60


def show_value(value: Any) -> str:
    """A value as the user wrote it, for messages: text in double quotes, the rest as JSON."""
    text = (
        f'"{value}"' if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    )
    return text if len(text) <= _SHOWN_CHARS else text[: _SHOWN_CHARS - 1] + "…"


def _options(col: DatasetColumn) -> str:
    return f"Options: {', '.join(col.options or [])}" if col.options else "This column has no options yet"


def type_error(col: DatasetColumn, value: Any) -> str | None:
    """Return a plain reason if `value` doesn't match `col.type`, else None (GL-148)."""
    if col.type in (ColumnType.TEXT, ColumnType.LONG_TEXT):
        if not isinstance(value, str):
            return f"{show_value(value)} is not text"
    elif col.type == ColumnType.NUMBER:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"{show_value(value)} is not a number"
    elif col.type == ColumnType.BOOLEAN:
        if not isinstance(value, bool):
            return f"{show_value(value)} is not yes or no"
    elif col.type == ColumnType.SELECT:
        if not isinstance(value, str) or value not in (col.options or []):
            return f"{show_value(value)} is not an option. {_options(col)}"
    elif col.type == ColumnType.MULTI_SELECT:
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return f"{show_value(value)} is not a list of options. {_options(col)}"
        bad = [v for v in value if v not in (col.options or [])]
        if bad:
            shown = ", ".join(show_value(v) for v in bad)
            verb = "is not an option" if len(bad) == 1 else "are not options"
            return f"{shown} {verb}. {_options(col)}"
    elif col.type == ColumnType.JSON:
        return _json_error(col, value)
    elif col.type == ColumnType.DATE and not is_date(value):
        return f"{show_value(value)} is not a date. Use YYYY-MM-DD"
    if col.is_key and isinstance(value, str) and value != value.strip():
        return "key value must not have leading or trailing whitespace"
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
        # For `json` only null is empty: "", [], {}, 0 and false are values.
        if value is None if col.type == ColumnType.JSON else is_empty(value):
            if col.required:
                errors.append((col.key, "required"))
            continue
        error = type_error(col, value)
        if error:
            errors.append((col.key, error))
    return errors


def describe_errors(errors: list[tuple[str, str]], limit: int = 3) -> str:
    """Row errors as one plain sentence: at most `limit` problems, then "and N more" (GL-135)."""
    shown = [
        f"{key}: {'is required' if reason == 'required' else reason}" for key, reason in errors[:limit]
    ]
    more = len(errors) - limit
    return "; ".join(shown) + (f"; and {more} more" if more > 0 else "")


def row_label(row: Any) -> str:
    """How a message names a row: its key value, else the start of its id."""
    return f"Row '{row.row_key}'" if row.row_key else f"Row {str(row.id)[:8]}"


def validate_row_data(data: dict[str, Any], columns: list[DatasetColumn]) -> None:
    """Raise a 422 with the first validation failure found, else return None."""
    errors = row_errors(data, columns)
    if errors:
        key, reason = errors[0]
        raise HTTPException(status_code=422, detail=f"column '{key}': {reason}")
