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


class SchemaRead(BaseModel):
    columns: list[Column]
    # Phase 4: the store entry a collection dataset uses (null when unfiled).
    store_schema: StoreSchemaRef | None = None
