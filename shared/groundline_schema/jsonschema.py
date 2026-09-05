"""JSON Schema sidecar generation (§7).

Turns a list of `Column` definitions into a JSON Schema document so any consumer,
in any language, can validate rows before use. Generated at export time and
shipped next to the data.
"""

from __future__ import annotations

from groundline_schema.column_types import Column


def build_json_schema(columns: list[Column]) -> dict:
    """Return a JSON Schema (draft 2020-12) for a row of the given columns.

    TODO: map each ColumnType to its JSON Schema fragment, mark required
    columns, and constrain select/multi_select to their options.
    """
    raise NotImplementedError
