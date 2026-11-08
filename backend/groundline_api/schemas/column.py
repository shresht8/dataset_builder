"""Schema (column-definition) payloads for GET/PUT /datasets/{id}/schema (§8).

Wraps `groundline_schema.Column` for the wire — the shared model is the single
source of truth for column typing. `order` is taken from list position on PUT
and `archived` is managed server-side (removal archives, re-add un-archives).
"""

from __future__ import annotations

from groundline_schema import Column
from pydantic import BaseModel

from groundline_api.schemas.dataset import StoreSchemaRef


class SchemaUpdate(BaseModel):
    columns: list[Column]


class SchemaProblem(BaseModel):
    column: str
    kind: str  # "required" | "type"
    rows: int
    examples: list[str]
    readded: bool


class SchemaDryRun(BaseModel):
    """`?dry_run=true`: what the change would do; nothing was written."""

    ok: bool
    detail: str | None = None  # the message a real write would be refused with
    problems: list[SchemaProblem] = []


class SchemaRead(BaseModel):
    columns: list[Column]
    # Phase 4: the store entry a collection dataset uses (null when unfiled).
    store_schema: StoreSchemaRef | None = None
