"""Export a version to JSONL / JSON / YAML plus a JSON Schema sidecar (§7).

JSON Schema is generated from the version's embedded column schema via
`groundline_schema.jsonschema.build_json_schema`.
"""

from __future__ import annotations

# TODO: export_version(version, fmt) -> bytes ; sidecar(version) -> dict
