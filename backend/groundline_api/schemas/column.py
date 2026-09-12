"""Schema (column-definition) payloads for GET/PUT /datasets/{id}/schema (§8).

Wraps `groundline_schema.Column` for the wire — the shared model is the single
source of truth for column typing. `order` is taken from list position on PUT
and `archived` is managed server-side (removal archives, re-add un-archives).
"""

from __future__ import annotations

from groundline_schema import Column
from pydantic import BaseModel


class SchemaUpdate(BaseModel):
    columns: list[Column]


class SchemaRead(BaseModel):
    columns: list[Column]
