"""Render a version's rows as jsonl / json / yaml, flat or nested (§7).

Shared by the API's export endpoint and the CLI's `pull`, so a file the CLI
renders from hash-verified jsonl bytes is byte-identical to what the API would
serve for the same version, format and shape (GL-3.5-13).
"""

from __future__ import annotations

import json

import yaml

from groundline_schema.paths import nest

FORMATS = ("jsonl", "json", "yaml")
SHAPES = ("flat", "nested")


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


def nest_rows(rows: list[dict]) -> list[dict]:
    """Each line with its `data` nested from dotted keys; `id`/`status` unchanged."""
    return [{**row, "data": nest(row["data"])} for row in rows]


def export_jsonl(rows: list[dict]) -> bytes:
    """One canonical line per row: sorted keys, compact separators (as stored)."""
    return b"".join(
        (json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")
        .encode("utf-8")
        for row in rows
    )


def export_json(manifest: dict, rows: list[dict]) -> bytes:
    payload = {"manifest": manifest, "rows": rows}
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def export_yaml(manifest: dict, rows: list[dict]) -> bytes:
    payload = {"manifest": manifest, "rows": rows}
    return yaml.dump(
        payload, Dumper=_BlockStyleDumper, allow_unicode=True, sort_keys=False
    ).encode("utf-8")


def render(rows_bytes: bytes, manifest: dict, fmt: str, shape: str) -> bytes:
    """Render stored `rows.jsonl` bytes in `fmt` and `shape`.

    Flat jsonl is the stored bytes unchanged, so its content_hash still holds.
    """
    if fmt == "jsonl" and shape == "flat":
        return rows_bytes
    rows = parse_rows_jsonl(rows_bytes)
    if shape == "nested":
        rows = nest_rows(rows)
    if fmt == "jsonl":
        return export_jsonl(rows)
    if fmt == "json":
        return export_json(manifest, rows)
    return export_yaml(manifest, rows)
