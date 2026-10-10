"""JSON Schema sidecar generation (§7).

Turns a list of `Column` definitions into a JSON Schema document so any consumer,
in any language, can validate rows before use. Generated at export time and
shipped next to the data.
"""

from __future__ import annotations

from groundline_schema.column_types import Column, ColumnType
from groundline_schema.temporal import DATE_PATTERN

# `services/validation.py::is_empty` treats None, "" (after strip) and [] as
# "empty" for every column type except `json` (where only null is empty),
# before it ever looks at the column's declared type. An optional column can therefore be stored as any of these three
# regardless of its type, in addition to a properly-typed value. The sidecar
# must accept all of it (C3: "must accept every row that passed row
# validation").
_EMPTY_VARIANTS: list[dict] = [{"type": "null"}, {"const": ""}, {"const": []}]


def _json_fragment(column: Column) -> dict:
    """A `json` column's own JSON Schema, or `{}` (any value) if it has none.

    `$schema` only belongs at a resource root, so it is dropped from the copy
    embedded here.
    """
    fragment = dict(column.json_schema or {})
    fragment.pop("$schema", None)
    return fragment


def _type_schema(column: Column) -> dict:
    """The JSON Schema fragment for a properly-typed, non-empty value."""
    if column.type == ColumnType.JSON:
        return _json_fragment(column)
    if column.type == ColumnType.DATE:
        # `format` is annotation-only by default in 2020-12, so the pattern is
        # what actually makes validators check the shape (GL-3.5-8).
        return {"type": "string", "format": "date", "pattern": DATE_PATTERN}
    if column.type in (ColumnType.TEXT, ColumnType.LONG_TEXT):
        return {"type": "string"}
    if column.type == ColumnType.NUMBER:
        return {"type": "number"}
    if column.type == ColumnType.BOOLEAN:
        return {"type": "boolean"}
    if column.type == ColumnType.SELECT:
        return {"type": "string", "enum": list(column.options or [])}
    if column.type == ColumnType.MULTI_SELECT:
        # validation.py never checks for duplicates, so no uniqueItems.
        return {
            "type": "array",
            "items": {"type": "string", "enum": list(column.options or [])},
        }
    raise ValueError(f"unknown column type: {column.type!r}")


def build_json_schema(columns: list[Column]) -> dict:
    """Return a JSON Schema (draft 2020-12) for a row's `data` object.

    Required columns get the plain type schema (validation never lets a
    required column store an empty value). Optional columns additionally
    accept null, "" and [] (see `_EMPTY_VARIANTS`). `data` holds only schema
    columns (C2), so `additionalProperties` is false.
    """
    properties: dict[str, dict] = {}
    required: list[str] = []
    for column in columns:
        type_schema = _type_schema(column)
        if column.type == ColumnType.JSON:
            # For `json` only null is empty (GL-3.5-13): "" and [] are values.
            if column.required:
                properties[column.key] = {"allOf": [type_schema, {"not": {"type": "null"}}]}
                required.append(column.key)
            else:
                properties[column.key] = {"anyOf": [type_schema, {"type": "null"}]}
            continue
        if column.required:
            # `is_empty` (validation.py) rejects "" and [] for a required
            # column regardless of type; the plain type schema alone would
            # still accept them (an empty string is a valid "string").
            prop = dict(type_schema)
            if column.type in (ColumnType.TEXT, ColumnType.LONG_TEXT):
                prop["minLength"] = 1
            elif column.type == ColumnType.MULTI_SELECT:
                prop["minItems"] = 1
            properties[column.key] = prop
            required.append(column.key)
        else:
            properties[column.key] = {"anyOf": [type_schema, *_EMPTY_VARIANTS]}

    schema: dict = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def nest_json_schema(flat_schema: dict) -> dict:
    """The nested-shape counterpart of a `build_json_schema` result (GL-3.5-13).

    Dotted property keys become nested object schemas with
    `additionalProperties: false` at every level. An intermediate object is
    required only if one of its descendants is.
    """
    required_keys = set(flat_schema.get("required", []))
    nested = {k: v for k, v in flat_schema.items() if k not in ("properties", "required")}
    nested["properties"] = {}
    for key, prop in flat_schema["properties"].items():
        segments = key.split(".")
        node = nested
        for index, segment in enumerate(segments):
            if key in required_keys:
                node.setdefault("required", [])
                if segment not in node["required"]:
                    node["required"].append(segment)
            if index == len(segments) - 1:
                node["properties"][segment] = prop
            else:
                node = node["properties"].setdefault(
                    segment,
                    {"type": "object", "properties": {}, "additionalProperties": False},
                )
    return nested
