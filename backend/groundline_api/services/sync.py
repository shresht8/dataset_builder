"""Key-based row sync: upsert from a file with conflict detection (GL-3.5-15).

Data scientists keep a dataset as a file and push it with the CLI while
annotators fix rows in the UI. Records are matched to rows on the dataset's
key column; for each existing key, in order:

  1. the record still hashes to the base the CLI pulled (`bases[key]`): it
     wasn't edited locally, so skip it -- `unchanged`, or `server_newer` when
     the row has moved on since the pull. Never a conflict, never written.
  2. no field differs from the row (null == absent): `unchanged`.
  3. `revs[key]` equals the row's rev, or `force`: `update`.
  4. otherwise: `conflict` (edited on both sides, or never pulled).

New keys are created as draft rows -- except a key that belongs only to a
deleted row and was pulled before the delete (it's in `revs`): that's reported
in `deleted_keys` and skipped, unless `force` recreates it (GL-3.5-18).

An update merges fields: a column in the
record overwrites, an absent column is left alone, an explicit null clears.
A changed row that was approved goes back to needs_review. Sync never deletes.

All or nothing: any conflict or error, or `dry_run`, and nothing is written.
The dataset row and every matched row are locked for the duration.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa
from groundline_schema.paths import row_hash
from groundline_schema.records import RecordProblem
from sqlalchemy import select
from sqlalchemy.orm import Session

from groundline_api.models.dataset import Dataset, DatasetColumn
from groundline_api.models.row import DatasetRow, RowEdit, RowStatus
from groundline_api.models.user import User
from groundline_api.services import importer
from groundline_api.services.validation import key_column, row_errors


class SyncRequestError(Exception):
    """A caller-facing (422) problem with the sync request itself."""


@dataclass
class SyncOptions:
    revs: dict[str, int] = field(default_factory=dict)
    bases: dict[str, str] = field(default_factory=dict)
    force: bool = False
    dry_run: bool = False
    ignore_unknown: bool = False


def _canonical(value: Any) -> str:
    """Compare values as JSON does: 1, 1.0 and true are different values."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def sync(
    db: Session,
    dataset_id: uuid.UUID,
    user: User,
    source: importer.ParsedSource,
    columns: list[DatasetColumn],
    options: SyncOptions,
) -> dict[str, Any]:
    key_col = key_column(columns)
    if key_col is None:
        raise SyncRequestError("dataset has no key column; mark one in the schema first")
    active_keys = {col.key for col in columns if not col.archived}
    columns_by_key = {col.key: col for col in columns}

    errors: list[dict[str, Any]] = []
    unknown = [path for path in source.header if path not in active_keys]
    if unknown and not options.ignore_unknown:
        errors += [
            {"key": None, "column": path,
             "reason": "no column with this key (fix the path, or use --ignore-unknown)"}
            for path in unknown
        ]
    mapping = {path: path for path in source.header if path in active_keys}

    # Serialise with cuts and other syncs before reading anything.
    db.execute(select(Dataset).where(Dataset.id == dataset_id).with_for_update())

    records: list[tuple[str, dict[str, Any], set[str]]] = []
    first_row: dict[str, int] = {}
    for index, values in enumerate(source.rows, start=1):
        if isinstance(values, RecordProblem):
            errors.append({"key": None, "row": index, "column": "", "reason": values.reason})
            continue
        data, failures = importer.map_row(values, mapping, columns_by_key, structured=True)
        cleared = {path for path, value in values.items() if value is None and path in mapping}
        key = data.get(key_col.key)
        if not isinstance(key, str) or key.strip() == "":
            errors.append(
                {"key": None, "row": index, "column": key_col.key, "reason": "missing key value"}
            )
            continue
        if key in first_row:
            errors.append({"key": key, "row": index, "column": key_col.key,
                           "reason": f"duplicate key in file (also row {first_row[key]})"})
            continue
        first_row[key] = index
        errors += [
            {"key": key, "row": index, "column": column, "reason": reason}
            for column, reason in failures
        ]
        records.append((key, data, cleared))

    existing = {
        row.row_key: row
        for row in db.scalars(
            select(DatasetRow)
            .where(
                DatasetRow.dataset_id == dataset_id,
                DatasetRow.row_key.in_(list(first_row)),
                DatasetRow.deleted_at.is_(None),
            )
            .with_for_update()
        )
    }
    # Keys the caller pulled that now belong only to a deleted row.
    pulled_missing = [k for k in first_row if k not in existing and k in options.revs]
    deleted_keys = set(
        db.scalars(
            select(DatasetRow.row_key).where(
                DatasetRow.dataset_id == dataset_id,
                DatasetRow.row_key.in_(pulled_missing),
                DatasetRow.deleted_at.is_not(None),
            )
        )
    ) if pulled_missing else set()

    creates: list[tuple[str, dict[str, Any]]] = []
    updates: list[tuple[DatasetRow, dict[str, Any], dict[str, tuple[Any, Any]]]] = []
    unchanged: list[DatasetRow] = []
    server_newer: list[str] = []
    skipped_deleted: list[str] = []
    changes: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    for key, data, cleared in records:
        row = existing.get(key)
        if row is None and key in deleted_keys and not options.force:
            skipped_deleted.append(key)
            continue
        if row is None:
            failures = row_errors(data, columns)
            errors += [{"key": key, "column": c, "reason": reason} for c, reason in failures]
            if not failures:
                creates.append((key, data))
                changes.append({
                    "key": key, "op": "create",
                    "fields": {c: {"before": None, "after": v} for c, v in data.items()},
                })
            continue

        if options.bases.get(key) == row_hash(data, active_keys):
            if options.revs.get(key) == row.rev:
                unchanged.append(row)
            else:
                server_newer.append(key)
            continue

        merged = {k: v for k, v in row.data.items() if k not in cleared}
        merged.update(data)
        changed = {
            column: (row.data.get(column), merged.get(column))
            for column in sorted(set(data) | cleared)
            if _canonical(row.data.get(column)) != _canonical(merged.get(column))
        }
        if not changed:
            unchanged.append(row)
            continue

        your_rev = options.revs.get(key)
        if your_rev != row.rev and not options.force:
            conflict = {
                "key": key, "row_id": str(row.id), "server_rev": row.rev, "your_rev": your_rev,
                "fields": {c: {"server": old, "file": new} for c, (old, new) in changed.items()},
            }
            if your_rev is None:
                conflict["reason"] = "exists in Groundline; run rows pull first or use --force"
            conflicts.append(conflict)
            continue

        failures = row_errors(merged, columns)
        errors += [{"key": key, "column": c, "reason": reason} for c, reason in failures]
        if not failures:
            updates.append((row, merged, changed))
            changes.append({
                "key": key, "op": "update",
                "fields": {c: {"before": old, "after": new} for c, (old, new) in changed.items()},
            })

    in_file = list(first_row)
    not_in_file = db.scalar(
        select(sa.func.count())
        .select_from(DatasetRow)
        .where(
            DatasetRow.dataset_id == dataset_id,
            DatasetRow.row_key.is_not(None),
            DatasetRow.deleted_at.is_(None),
            DatasetRow.row_key.not_in(in_file) if in_file else sa.true(),
        )
    )

    applied = not conflicts and not errors and not options.dry_run
    revs: dict[str, int] = {}
    bases: dict[str, str] = {}
    if applied:
        started = datetime.now(timezone.utc)
        created_rows = []
        for position, (key, data) in enumerate(creates):
            row = DatasetRow(
                dataset_id=dataset_id, data=data, row_key=key, rev=1, updated_by=user.id,
                created_at=started + timedelta(microseconds=position),  # keep the file's order
            )
            db.add(row)
            created_rows.append(row)
        for row, merged, changed in updates:
            for column, (old, new) in changed.items():
                db.add(RowEdit(row_id=row.id, field=column, old_value=old, new_value=new,
                               user_id=user.id))
            if row.status == RowStatus.APPROVED:
                db.add(RowEdit(row_id=row.id, field="status", old_value=RowStatus.APPROVED.value,
                               new_value=RowStatus.NEEDS_REVIEW.value, user_id=user.id))
                row.status = RowStatus.NEEDS_REVIEW
            row.data = merged
            row.rev = row.rev + 1
            row.updated_by = user.id
        db.commit()
        for row in [*created_rows, *(u[0] for u in updates), *unchanged]:
            revs[row.row_key] = row.rev
            bases[row.row_key] = row_hash(row.data, active_keys)
    else:
        db.rollback()  # release the locks; nothing was written

    return {
        "applied": applied,
        "created": len(creates),
        "updated": len(updates),
        "unchanged": len(unchanged),
        "server_newer": len(server_newer),
        "not_in_file": not_in_file,
        "changes": changes,
        "conflicts": conflicts,
        "server_newer_keys": server_newer,
        "deleted_keys": skipped_deleted,
        "errors": errors,
        "ignored_paths": unknown if options.ignore_unknown else [],
        "revs": revs,
        "bases": bases,
    }
