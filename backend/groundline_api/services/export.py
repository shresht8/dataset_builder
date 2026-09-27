"""Export a version to JSONL / JSON / YAML plus a JSON Schema sidecar (§7).

JSON Schema is generated from the version's embedded column schema via
`groundline_schema.jsonschema.build_json_schema`. All three formats carry the
same content; jsonl is the raw stored bytes, json/yaml wrap the parsed lines
with the stored manifest (C3).
"""

from __future__ import annotations

import json

import yaml
from groundline_schema import Column
from groundline_schema.jsonschema import build_json_schema

from groundline_api.models.row import RowStatus


class _BlockStyleDumper(yaml.SafeDumper):
    """Emits multi-line strings as block scalars (`|`), per §7."""


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_BlockStyleDumper.add_representer(str, _represent_str)


def parse_rows_jsonl(rows_bytes: bytes) -> list[dict]:
    """Parse stored `rows.jsonl` bytes into line objects (C2 line shape)."""
    text = rows_bytes.decode("utf-8")
    return [json.loads(line) for line in text.splitlines() if line]


def export_json(manifest: dict, rows: list[dict]) -> bytes:
    payload = {"manifest": manifest, "rows": rows}
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def export_yaml(manifest: dict, rows: list[dict]) -> bytes:
    payload = {"manifest": manifest, "rows": rows}
    return yaml.dump(
        payload, Dumper=_BlockStyleDumper, allow_unicode=True, sort_keys=False
    ).encode("utf-8")


def sidecar_schema(columns: list[Column]) -> dict:
    """The JSON Schema sidecar for one snapshot line: id, status, data (C3)."""
    data_schema = build_json_schema(columns)
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
