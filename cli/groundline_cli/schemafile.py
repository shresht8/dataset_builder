"""Schema files: draft inference from data, load, and write (GL-3.5-16).

A schema file is YAML (or JSON) holding the API's column list::

    columns:
    - key: id
      label: Id
      type: text
      required: true
      is_key: true

`infer_columns` drafts one from records parsed by the shared reader. The
inference rules live in `groundline_schema.infer` (GL-117), shared with
`POST /v1/schema/infer`; this module renders and loads the YAML.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from groundline_schema import Column
from groundline_schema.infer import NULLS_ONLY, InferError
from groundline_schema.infer import infer_columns as _infer_columns
from pydantic import ValidationError

__all__ = [
    "NULLS_ONLY",
    "SchemaFileError",
    "infer_columns",
    "load_schema_file",
    "render_schema",
    "wire_columns",
]


class SchemaFileError(Exception):
    """A user-facing problem with a schema file or an inference request."""


def infer_columns(records: list[dict], key: str | None = None) -> tuple[list[dict], list[str]]:
    """Draft columns for `records` (shared rules). Returns (columns, warnings)."""
    try:
        return _infer_columns(records, key)
    except InferError as exc:
        raise SchemaFileError(str(exc)) from None


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
