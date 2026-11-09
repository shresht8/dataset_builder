"""Draft a column schema from records (GL-3.5-16; shared since GL-117).

Used by `groundline schema infer` and `POST /v1/schema/infer`, so the CLI and
the web wizard draft the same columns from the same file. One column per
flattened path, in first-seen order:

  - all booleans -> boolean; all numbers -> number;
  - all strings -> date (all `YYYY-MM-DD`), long_text (any newline or over
    200 chars), else text;
  - lists of strings -> multi_select (options: the distinct values seen);
  - objects, lists of other values, mixed types -> json;
  - only nulls seen -> text, flagged for review (`_comment: NULLS_ONLY`).
"""

from __future__ import annotations

import re
from typing import Any

from groundline_schema.column_types import ColumnType
from groundline_schema.paths import flatten
from groundline_schema.records import to_json_value

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_HAS_DATE_TYPE = "date" in {member.value for member in ColumnType}
_LONG_TEXT_CHARS = 200
NULLS_ONLY = "only nulls seen - check type"
NO_KEY = "no key column found: rows push needs one (use --key FIELD)"


class InferError(ValueError):
    """A user-facing problem with an inference request (e.g. an unusable key)."""


def humanise(path: str) -> str:
    """`expected.golden_response` -> "Golden response"."""
    words = re.sub(r"[_\-]+", " ", path.split(".")[-1]).strip()
    return words[:1].upper() + words[1:] if words else path


def _typed(value: Any) -> Any:
    try:
        return to_json_value(value)
    except ValueError:  # .inf / .nan: not JSON, so not a number either
        return str(value)


def _column_type(values: list[Any]) -> tuple[str, list[str] | None]:
    if all(isinstance(v, bool) for v in values):
        return "boolean", None
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
        return "number", None
    if all(isinstance(v, str) for v in values):
        if _HAS_DATE_TYPE and all(_DATE.fullmatch(v) for v in values):
            return "date", None
        if any("\n" in v or len(v) > _LONG_TEXT_CHARS for v in values):
            return "long_text", None
        return "text", None
    if all(isinstance(v, list) and all(isinstance(i, str) for i in v) for v in values):
        options = sorted({item for v in values for item in v})
        if options:
            return "multi_select", options
    return "json", None


def _usable_key(values: list[Any], present: int, total: int) -> bool:
    return (
        present == total
        and all(isinstance(v, str) and v.strip() == v and v for v in values)
        and len(set(values)) == len(values)
    )


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _why_not_key(field: str, values: list[Any], present: int, total: int) -> str:
    """Why `field` failed `_usable_key`, for the warning next to NO_KEY (GL-146)."""
    reasons: list[str] = []
    missing = total - present + sum(1 for v in values if isinstance(v, str) and not v.strip())
    if missing:
        reasons.append(f"{_plural(missing, 'record')} without one")
    texts = [v for v in values if isinstance(v, str) and v.strip()]
    seen: set[str] = set()
    repeated: list[str] = []
    for value in texts:
        if value in seen and value not in repeated:
            repeated.append(value)
        seen.add(value)
    if repeated:
        shown = ", ".join(f'"{v}"' for v in repeated[:3])
        more = f" and {len(repeated) - 3} more" if len(repeated) > 3 else ""
        reasons.append(f"repeated values {shown}{more}")
    not_text = sum(1 for v in values if not isinstance(v, str))
    if not_text:
        reasons.append(f"{_plural(not_text, 'value')} not text")
    padded = sum(1 for v in texts if v.strip() != v)
    if padded:
        reasons.append(f"{_plural(padded, 'value')} with leading or trailing spaces")
    return f"\"{field}\" can't be the key: " + "; ".join(reasons)


def infer_columns(records: list[dict], key: str | None = None) -> tuple[list[dict], list[str]]:
    """Draft columns for `records`. Returns (columns, warnings).

    A column whose values were all null carries `_comment: NULLS_ONLY`.
    """
    values_by_path: dict[str, list[Any]] = {}
    present: dict[str, int] = {}
    for record in records:
        for path, value in flatten(record).items():
            values_by_path.setdefault(path, [])
            if value is not None:
                values_by_path[path].append(_typed(value))
                present[path] = present.get(path, 0) + 1

    warnings: list[str] = []
    if key is not None:
        if key not in values_by_path:
            raise InferError(f"--key '{key}' is not a field in the file")
        if not _usable_key(values_by_path[key], present.get(key, 0), len(records)):
            raise InferError(
                f"--key '{key}' must be a non-empty, unique text value in every record"
            )
    elif "id" in values_by_path and _usable_key(
        values_by_path["id"], present.get("id", 0), len(records)
    ):
        key = "id"
    else:
        warnings.append(NO_KEY)
        if "id" in values_by_path:
            warnings.append(
                _why_not_key("id", values_by_path["id"], present.get("id", 0), len(records))
            )

    columns: list[dict] = []
    for path, values in values_by_path.items():
        column: dict[str, Any] = {"key": path, "label": humanise(path)}
        if path == key:
            column.update(type="text", required=True, is_key=True)
        elif not values:
            column.update(type="text", required=False, _comment=NULLS_ONLY)
        else:
            column_type, options = _column_type(values)
            column["type"] = column_type
            if options is not None:
                column["options"] = options
            column["required"] = False
        columns.append(column)
    return columns, warnings
