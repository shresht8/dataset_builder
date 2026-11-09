"""Reject schema changes that would break stored rows (GL-134, reject-only).

`check` runs once, at the top of `columns.write_columns` -- the only code that
changes `dataset_columns` -- so the schema editor, picking another store entry,
dataset create and the CLI are all covered. It compares `row_errors` of every
live row under the current columns and under the columns the write would leave
behind; only (row, column) pairs that are *new* count, so rows that were already
broken never block an unrelated schema edit. Whole `row.data` is checked, so the
values an archived column still holds are caught when its key comes back.

Left out on purpose: soft-deleted rows (every row read filters them), and the new
key column (the key check in `columns.write_columns` already explains that case).

The 422 `detail` is a plain string (the web shows it as the error message); the
same problems are also available as data for `?dry_run=true`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from groundline_schema import Column, ColumnType
from sqlalchemy import select
from sqlalchemy.orm import Session

from groundline_api.models.dataset import DatasetColumn
from groundline_api.models.row import DatasetRow
from groundline_api.services.validation import row_errors

_EXAMPLES = 3
_TEXTS = {ColumnType.TEXT, ColumnType.LONG_TEXT}


@dataclass
class ColumnProblem:
    column: str
    kind: str  # "required" | "type"
    rows: int = 0
    examples: list[str] = field(default_factory=list)
    readded: bool = False
    old_type: str = ""
    new_type: str = ""
    # Can the stored values be edited into the new type before the change?
    fixable: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "kind": self.kind,
            "rows": self.rows,
            "examples": self.examples,
            "readded": self.readded,
        }


class SchemaImpactError(HTTPException):
    """422 with a plain-string detail; `problems` carries the same facts as data."""

    def __init__(self, problems: list[ColumnProblem]) -> None:
        super().__init__(status_code=422, detail=_message(problems))
        self.problems = problems


def _fixable(old: ColumnType, new: ColumnType) -> bool:
    """Can a value in an `old` column be edited into one that fits `new`, before the change?

    Each editor writes one kind of value (text: str, number: number, boolean: bool,
    multi_select: list, select: one of its options, date: YYYY-MM-DD, json: anything),
    and `type_error` accepts exactly that kind -- so most changes between text,
    number, boolean and multi_select are impossible in place.
    """
    if old == new or old == ColumnType.JSON or new == ColumnType.JSON:
        return True
    if old in _TEXTS and new in (ColumnType.SELECT, ColumnType.DATE):
        return True
    return old in _TEXTS and new in _TEXTS


def _shown(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 40 else text[:37] + "..."


def _transient(columns: list[Column]) -> list[DatasetColumn]:
    return [
        DatasetColumn(
            key=col.key,
            label=col.label,
            type=col.type,
            options=col.options,
            required=col.required,
            order=position,
            archived=False,
            json_schema=col.json_schema,
            is_key=col.is_key,
        )
        for position, col in enumerate(columns)
    ]


def _plural(n: int, word: str = "row") -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _message(problems: list[ColumnProblem]) -> str:
    parts = []
    for p in problems:
        e = ", ".join(p.examples)
        n = _plural(p.rows)
        if p.kind == "required":
            if p.readded:
                parts.append(
                    f"Column '{p.column}' was archived and has no value in {n} (e.g. {e}), "
                    "but it would be required. Use a different key for the new column, or keep it optional."
                )
            else:
                parts.append(
                    f"Column '{p.column}' would be required but has no value in {n} (e.g. {e}). "
                    "Fill them in first, or keep it optional."
                )
        elif p.readded:
            parts.append(
                f"Column '{p.column}' was archived and still holds values in {n} (e.g. {e}) that "
                f"don't fit {p.new_type}. Use a different key for the new column, or keep the type {p.old_type}."
            )
        elif p.fixable:
            parts.append(
                f"Column '{p.column}': {n} (e.g. {e}) don't fit {p.new_type}. "
                "Fix these rows first, then change the schema."
            )
        else:
            parts.append(
                f"Values in column '{p.column}' ({n}, e.g. {e}) can't be converted in place "
                f"from {p.old_type} to {p.new_type}. Use a new key for the new column and import "
                "the values again, or delete those rows."
            )
    return "; ".join(parts)


def find_problems(db: Session, dataset_id: uuid.UUID, columns: list[Column]) -> list[ColumnProblem]:
    current = list(db.scalars(select(DatasetColumn).where(DatasetColumn.dataset_id == dataset_id)))
    rows = list(
        db.scalars(
            select(DatasetRow).where(
                DatasetRow.dataset_id == dataset_id, DatasetRow.deleted_at.is_(None)
            )
        )
    )
    if not rows:
        return []
    old_by_key = {c.key: c for c in current}
    old_key = next((c.key for c in current if c.is_key and not c.archived), None)
    new_key = next((c.key for c in columns if c.is_key), None)
    skip_key = new_key if new_key != old_key else None
    new_columns = _transient(columns)
    new_by_key = {c.key: c for c in columns}

    found: dict[tuple[str, str], ColumnProblem] = {}
    for row in rows:
        before = set(row_errors(row.data, current))
        label = row.row_key or str(row.id)[:8]
        for key, reason in row_errors(row.data, new_columns):
            if (key, reason) in before or key == skip_key:
                continue
            kind = "required" if reason == "required" else "type"
            problem = found.get((key, kind))
            if problem is None:
                previous = old_by_key.get(key)
                new_type = new_by_key[key].type
                problem = found[(key, kind)] = ColumnProblem(
                    column=key,
                    kind=kind,
                    readded=previous is not None and previous.archived,
                    old_type=previous.type.value if previous is not None else "",
                    new_type=new_type.value,
                    fixable=kind == "required"
                    or previous is None
                    or _fixable(previous.type, new_type),
                )
            problem.rows += 1
            if len(problem.examples) < _EXAMPLES:
                value = row.data.get(key)
                if kind == "required":
                    problem.examples.append(label)
                elif row.row_key:
                    problem.examples.append(f"{row.row_key}: {_shown(value)}")
                else:  # no key column: a row id would mean nothing to the reader
                    problem.examples.append(_shown(value))
    order = {col.key: i for i, col in enumerate(columns)}
    return sorted(found.values(), key=lambda p: (order[p.column], p.kind))


def check(db: Session, dataset_id: uuid.UUID, columns: list[Column]) -> None:
    """Raise `SchemaImpactError` if writing `columns` would newly break a stored row."""
    problems = find_problems(db, dataset_id, columns)
    if problems:
        raise SchemaImpactError(problems)
