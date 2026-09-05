"""Groundline shared schema.

Column types, dataset column definitions, the version manifest shape, and the
JSON Schema sidecar generator. Imported by both the API (`groundline_api`) and
the CLI (`groundline_cli`) so the two never disagree about the data model.

Design ref: docs/groundline-dataset-builder.md §3 (data model), §7 (export).
"""

from groundline_schema.column_types import Column, ColumnType

__all__ = ["Column", "ColumnType"]
