"""Schema files: draft inference from data, load, and write (GL-3.5-16).

A schema file is YAML (or JSON) holding the API's column list::

    columns:
    - key: id
      label: Id
      type: text
      required: true
      is_key: true

`infer_columns` drafts one from records parsed by the shared reader, one
column per flattened path in first-seen order:

  - all booleans -> boolean; all numbers -> number;
  - all strings -> date (all `YYYY-MM-DD`, once the type exists), long_text
    (any newline or over 200 chars), else text;
  - lists of strings -> multi_select (options: the distinct values seen);
  - objects, lists of other values, mixed types -> json;
  - only nulls seen -> text, flagged for review.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml
from groundline_schema import Column, ColumnType
from groundline_schema.paths import flatten
from groundline_schema.records import to_json_value
from pydantic import ValidationError

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_HAS_DATE_TYPE = "date" in {member.value for member in ColumnType}
_LONG_TEXT_CHARS = 200
NULLS_ONLY = "only nulls seen - check type"


class SchemaFileError(Exception):
    """A user-facing problem with a schema file or an inference request."""


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


def infer_columns(records: list[dict], key: str | None = None) -> tuple[list[dict], list[str]]:
    """Draft columns for `records`. Returns (columns, warnings)."""
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
            raise SchemaFileError(f"--key '{key}' is not a field in the file")
        if not _usable_key(values_by_path[key], present.get(key, 0), len(records)):
            raise SchemaFileError(
                f"--key '{key}' must be a non-empty, unique text value in every record"
            )
    elif "id" in values_by_path and _usable_key(
        values_by_path["id"], present.get("id", 0), len(records)
    ):
        key = "id"
    else:
        warnings.append("no key column found: rows push needs one (use --key FIELD)")

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


def _usable_key(values: list[Any], present: int, total: int) -> bool:
    return (
        present == total
        and all(isinstance(v, str) and v.strip() == v and v for v in values)
        and len(set(values)) == len(values)
    )


def render_schema(columns: list[dict], header: str | None = None) -> str:
    """YAML text for a schema file; a column's `_comment` becomes a comment line."""
    lines = [f"# {header}"] if header else []
    lines.append("columns:")
    for column in columns:
        comment = column.get("_comment")
        if comment:
            lines.append(f"# {column['key']}: {comment}")
        body = {k: v for k, v in column.items() if not k.startswith("_")}
        lines.append(yaml.safe_dump([body], sort_keys=False, allow_unicode=True).rstrip("\n"))
    return "\n".join(lines) + "\n"


def wire_columns(columns: list[dict]) -> list[dict]:
    """Server columns as a schema file lists them (no server-managed fields)."""
    out = []
    for column in columns:
        item = {"key": column["key"], "label": column["label"], "type": column["type"]}
        if column.get("options") is not None:
            item["options"] = column["options"]
        item["required"] = bool(column.get("required"))
        if column.get("is_key"):
            item["is_key"] = True
        if column.get("json_schema") is not None:
            item["json_schema"] = column["json_schema"]
        out.append(item)
    return out


def load_schema_file(path: Path) -> list[dict]:
    """Read and validate a schema file against the shared Column model."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaFileError(f"can't read {path}: {exc}") from None
    try:
        doc = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise SchemaFileError(f"{path} is not valid {path.suffix.lstrip('.') or 'YAML'}: {exc}")
    columns = doc.get("columns") if isinstance(doc, dict) else doc
    if not isinstance(columns, list):
        raise SchemaFileError(f"{path}: expected a 'columns' list")
    validated = []
    for index, column in enumerate(columns, start=1):
        try:
            validated.append(Column.model_validate(column).model_dump(mode="json"))
        except ValidationError as exc:
            problem = "; ".join(
                f"{'.'.join(str(p) for p in error['loc'])}: {error['msg']}" for error in exc.errors()
            )
            raise SchemaFileError(f"{path}: column {index}: {problem}") from None
    return validated
