"""Export a version to JSONL / JSON / YAML plus a JSON Schema sidecar (§7).

JSON Schema is generated from the version's embedded column schema via
`groundline_schema.jsonschema.build_json_schema`. All three formats carry the
same content; jsonl is the raw stored bytes, json/yaml wrap the parsed lines
with the stored manifest (C3). Rendering lives in `groundline_schema.render`
so the CLI produces byte-identical files from verified jsonl (GL-3.5-13); the
`nested` shape nests each line's `data` from its dotted keys.
"""

from __future__ import annotations

from groundline_schema import Column
from groundline_schema.jsonschema import build_json_schema, nest_json_schema
from groundline_schema.render import parse_rows_jsonl, render

from groundline_api.models.row import RowStatus

__all__ = ["parse_rows_jsonl", "render", "sidecar_schema"]


def sidecar_schema(columns: list[Column], shape: str = "flat") -> dict:
    """The JSON Schema sidecar for one snapshot line: id, status, data (C3)."""
    data_schema = build_json_schema(columns)
    if shape == "nested":
        data_schema = nest_json_schema(data_schema)
    # In 2020-12, `$schema` belongs only at a schema resource's root; the
    # embedded `data` schema must not repeat it.
    data_schema.pop("$schema", None)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "status": {"type": "string", "enum": [s.value for s in RowStatus]},
            "data": data_schema,
        },
        "required": ["id", "status", "data"],
        "additionalProperties": False,
    }
