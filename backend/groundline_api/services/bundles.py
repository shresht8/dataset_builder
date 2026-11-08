"""Collection bundle export and import (Phase 4, GL-122: D12, D13, C4).

Export builds a `groundline_schema.bundle.Bundle` from a collection: its
folders, its store, and each dataset's rows -- the live draft rows, or each
dataset's latest version read from its snapshot (`skipped` when it has none).

Import is two-step. `plan` checks everything without writing: every store
entry with the dataset-schema rules, every dataset's store reference, and
every row against its store schema, parsed and mapped exactly as import and
sync do (records are matched to columns by path). `commit` then creates the
collection, entries, folders, datasets and rows (as draft) in one transaction;
any error means nothing is created.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from groundline_schema import Column
from groundline_schema.bundle import (
    Bundle,
    BundleDataset,
    records_for,
    render_records,
    wire_column,
)
from groundline_schema.render import parse_rows_jsonl
from sqlalchemy import select
from sqlalchemy.orm import Session

from groundline_api.models.collection import Collection, CollectionSchema, Folder
from groundline_api.models.dataset import Dataset, DatasetColumn
from groundline_api.models.row import DatasetRow
from groundline_api.models.user import User
from groundline_api.models.version import DatasetVersion
from groundline_api.services import importer, storage, store
from groundline_api.services.columns import active_wire_columns, validate_columns
from groundline_api.services.paths import Paths
from groundline_api.services.validation import key_column

_MAX_ROW_ERRORS = 50


# --- Export ------------------------------------------------------------------


def export_bundle(db: Session, collection: Collection, source: str) -> Bundle:
    paths = Paths(db)
    folders = list(db.scalars(select(Folder).where(Folder.collection_id == collection.id)))
    folder_paths = sorted(
        ("/".join(paths.folder_segments(collection.id, f.id)) for f in folders),
        key=lambda p: (p.count("/"), p),
    )
    entries = db.scalars(
        select(CollectionSchema)
        .where(CollectionSchema.collection_id == collection.id)
        .order_by(CollectionSchema.name)
    )
    bundle = Bundle(
        name=collection.name,
        description=collection.description,
        source=source,
        exported_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        folders=folder_paths,
        store={e.name: [wire_column(c) for c in store.entry_columns(e)] for e in entries},
    )

    datasets = list(db.scalars(select(Dataset).where(Dataset.collection_id == collection.id)))
    rel = {d.id: "/".join([*paths.folder_segments(collection.id, d.folder_id), d.name]) for d in datasets}
    for dataset in sorted(datasets, key=lambda d: rel[d.id]):
        version_number = None
        if source == "latest":
            version = db.scalar(
                select(DatasetVersion)
                .where(DatasetVersion.dataset_id == dataset.id)
                .order_by(DatasetVersion.version.desc())
                .limit(1)
            )
            if version is None:
                bundle.skipped.append({"path": rel[dataset.id], "reason": "no versions"})
                continue
            lines = parse_rows_jsonl(storage.get_snapshot_file(version.snapshot_uri, "rows.jsonl"))
            data = [line["data"] for line in lines]
            columns = version.schema_snapshot["columns"]
            version_number = version.version
        else:
            data = list(
                db.scalars(
                    select(DatasetRow.data)
                    .where(DatasetRow.dataset_id == dataset.id, DatasetRow.deleted_at.is_(None))
                    .order_by(DatasetRow.created_at, DatasetRow.id)
                )
            )
            columns = [c.model_dump(mode="json") for c in active_wire_columns(db, dataset.id)]
        bundle.datasets.append(
            BundleDataset(
                path=rel[dataset.id],
                store_schema=paths.store_schema_name(dataset.store_schema_id),
                description=dataset.description,
                version=version_number,
                content=render_records(records_for(data, columns)),
            )
        )
    return bundle


# --- Import ------------------------------------------------------------------


@dataclass
class DatasetPlan:
    dataset: BundleDataset
    rows: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ImportPlan:
    bundle: Bundle
    name_taken: bool
    errors: list[str] = field(default_factory=list)
    store_errors: dict[str, str] = field(default_factory=dict)
    datasets: list[DatasetPlan] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and not self.store_errors and not any(d.errors for d in self.datasets)

    def report(self) -> dict[str, Any]:
        b = self.bundle
        return {
            "name": b.name,
            "name_taken": self.name_taken,
            "description": b.description,
            "source": b.source,
            "folders": b.folders,
            "store": [
                {"name": name, "columns": len(cols), "error": self.store_errors.get(name)}
                for name, cols in b.store.items()
            ],
            "datasets": [
                {
                    "path": d.dataset.path,
                    "store_schema": d.dataset.store_schema,
                    "description": d.dataset.description,
                    "rows": d.total,
                    "valid": len(d.rows),
                    "error_count": len(d.errors),
                    "errors": d.errors[:_MAX_ROW_ERRORS],
                }
                for d in self.datasets
            ],
            "errors": self.errors,
            "ok": self.ok,
        }


def _transient_columns(columns: list[dict[str, Any]]) -> list[DatasetColumn]:
    """Unsaved column objects, so the importer and validator can check rows."""
    out = []
    for position, raw in enumerate(columns):
        col = Column.model_validate(raw)
        out.append(
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
        )
    return out


def _plan_dataset(ds: BundleDataset, columns: list[dict[str, Any]]) -> DatasetPlan:
    plan = DatasetPlan(dataset=ds)
    orm_columns = _transient_columns(columns)
    try:
        source = importer.parse_upload(f"{ds.name}.yaml", ds.content, orm_columns)
    except importer.ImportRequestError as exc:
        plan.errors.append({"row": 0, "column": "", "reason": str(exc)})
        return plan
    keys = {c.key for c in orm_columns}
    rows = list(source.rows)
    plan.total = len(rows)
    source.rows = iter(rows)
    plan.errors += [
        {"row": 0, "column": path, "reason": "no column with this key in the store schema"}
        for path in source.header
        if path not in keys
    ]
    mapping = {path: path for path in source.header if path in keys}
    created, _imported, _skipped, errors = importer.commit(source, orm_columns, mapping, set(), {})
    plan.errors += errors
    plan.rows = created
    return plan


def plan(db: Session, bundle: Bundle) -> ImportPlan:
    taken = db.scalar(select(Collection.id).where(Collection.name == bundle.name)) is not None
    result = ImportPlan(bundle=bundle, name_taken=taken)
    for name, columns in bundle.store.items():
        try:
            validate_columns([Column.model_validate(c) for c in columns])
        except HTTPException as exc:
            result.store_errors[name] = str(exc.detail)
    for ds in bundle.datasets:
        if ds.store_schema not in bundle.store:
            result.errors.append(f"{ds.path}: store schema '{ds.store_schema}' is not in _store/")
            result.datasets.append(DatasetPlan(dataset=ds))
            continue
        if ds.store_schema in result.store_errors:
            result.datasets.append(DatasetPlan(dataset=ds))
            continue
        result.datasets.append(_plan_dataset(ds, bundle.store[ds.store_schema]))
    return result


def commit(db: Session, import_plan: ImportPlan, user: User) -> Collection:
    """Create everything in the plan, flushing as it goes. The caller commits."""
    bundle = import_plan.bundle
    collection = Collection(name=bundle.name, description=bundle.description, created_by=user.id)
    db.add(collection)
    db.flush()

    entries = {
        name: store.create_entry(
            db, collection.id, name, [Column.model_validate(c) for c in columns], user
        )
        for name, columns in bundle.store.items()
    }
    folder_ids: dict[str, uuid.UUID] = {}
    for path in bundle.folders:
        parent, _, name = path.rpartition("/")
        folder = Folder(
            collection_id=collection.id,
            parent_id=folder_ids[parent] if parent else None,
            name=name,
            created_by=user.id,
        )
        db.add(folder)
        db.flush()
        folder_ids[path] = folder.id

    started = datetime.now(timezone.utc)
    position = 0
    for dataset_plan in import_plan.datasets:
        ds = dataset_plan.dataset
        entry = entries[ds.store_schema]
        dataset = Dataset(
            name=ds.name,
            description=ds.description,
            collection_id=collection.id,
            folder_id=folder_ids[ds.folder] if ds.folder else None,
            store_schema_id=entry.id,
            created_by=user.id,
        )
        db.add(dataset)
        db.flush()
        store.apply(db, dataset, entry)
        key_col = key_column(_transient_columns(entry.columns))
        for data in dataset_plan.rows:
            db.add(
                DatasetRow(
                    dataset_id=dataset.id,
                    data=data,
                    updated_by=user.id,
                    row_key=data[key_col.key] if key_col is not None else None,
                    # Spaced out so rows keep the file's order (created_at order).
                    created_at=started + timedelta(microseconds=position),
                )
            )
            position += 1
    db.flush()
    return collection
