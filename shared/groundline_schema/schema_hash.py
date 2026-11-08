"""Canonical schema hash (Phase 4, C3).

Two schemas are "the same" only if their hashes are equal: the same active
columns, in the same order, with the same wire fields. The schema store uses
this to reuse an existing entry instead of adding a duplicate (D7).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from groundline_schema.column_types import Column

_WIRE_FIELDS = ("key", "label", "type", "options", "required", "json_schema", "is_key")


def canonical_columns(columns: list[Column | dict[str, Any]]) -> list[dict[str, Any]]:
    """Active columns reduced to their wire fields, in order.

    A null field is left out, and so is `is_key` when false.
    """
    out = []
    for column in columns:
        col = column if isinstance(column, Column) else Column.model_validate(column)
        if col.archived:
            continue
        dumped = col.model_dump(mode="json")
        item = {f: dumped[f] for f in _WIRE_FIELDS if dumped[f] is not None}
        if not item.get("is_key"):
            item.pop("is_key", None)
        out.append(item)
    return out


def schema_hash(columns: list[Column | dict[str, Any]]) -> str:
    """`"sha256:" + sha256(canonical JSON)` of the active columns (C3)."""
    text = json.dumps(
        canonical_columns(columns), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
