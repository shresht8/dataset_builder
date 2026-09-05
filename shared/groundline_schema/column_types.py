"""The six column types and the column definition model (§3).

The type set is deliberately small; see the design doc for why. This module is
the single source of truth for what a column is, shared by API and CLI.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class ColumnType(str, Enum):
    TEXT = "text"
    LONG_TEXT = "long_text"
    SELECT = "select"
    MULTI_SELECT = "multi_select"
    NUMBER = "number"
    BOOLEAN = "boolean"


class Column(BaseModel):
    """A single column in a dataset schema.

    `options` is required for `select` / `multi_select` and ignored otherwise.
    `archived` columns are retired but preserved in old version snapshots.
    """

    key: str
    label: str
    type: ColumnType
    options: list[str] | None = None
    required: bool = False
    order: int = 0
    archived: bool = False
