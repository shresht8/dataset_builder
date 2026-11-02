"""GL-3.5-12: `json` column type + key column — shared model and storage."""

from __future__ import annotations

import uuid

import pytest
from groundline_api.db import SessionLocal
from groundline_api.models.dataset import Dataset, DatasetColumn
from groundline_api.models.row import DatasetRow
from groundline_schema import ColumnType
from sqlalchemy.exc import IntegrityError

TOOL_PARAMS_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"name": {"type": "string"}, "args": {"type": "object"}},
        "required": ["name", "args"],
    },
}


def _create_dataset(client, as_role) -> str:
    as_role("editor")
    resp = client.post("/v1/datasets", json={"name": f"gl3512-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _delete_datasets(*ids: str) -> None:
    db = SessionLocal()
    try:
        for ds_id in ids:
            ds = db.get(Dataset, uuid.UUID(ds_id))
            if ds is not None:
                db.delete(ds)  # columns and rows cascade via FK
        db.commit()
    finally:
        db.close()


@pytest.fixture()
def dataset_id(client, as_role):
    ds_id = _create_dataset(client, as_role)
    yield ds_id
    _delete_datasets(ds_id)


@pytest.fixture()
def second_dataset_id(client, as_role):
    ds_id = _create_dataset(client, as_role)
    yield ds_id
    _delete_datasets(ds_id)


def test_shared_model_has_json_type_and_column_fields():
    assert ColumnType("json") is ColumnType.JSON


def test_put_schema_round_trips_json_schema_and_key(client, dataset_id):
    columns = [
        {"key": "id", "label": "ID", "type": "text", "required": True, "is_key": True},
        {
            "key": "expected.tool_params",
            "label": "Tool params",
            "type": "json",
            "json_schema": TOOL_PARAMS_SCHEMA,
        },
    ]
    resp = client.put(f"/v1/datasets/{dataset_id}/schema", json={"columns": columns})
    assert resp.status_code == 200, resp.text

    got = client.get(f"/v1/datasets/{dataset_id}/schema").json()["columns"]
    by_key = {c["key"]: c for c in got}
    assert by_key["id"]["is_key"] is True
    assert by_key["id"]["json_schema"] is None
    assert by_key["expected.tool_params"]["type"] == "json"
    assert by_key["expected.tool_params"]["json_schema"] == TOOL_PARAMS_SCHEMA
    assert by_key["expected.tool_params"]["is_key"] is False


def test_archiving_the_key_column_clears_is_key(client, dataset_id):
    key_col = {"key": "id", "label": "ID", "type": "text", "required": True, "is_key": True}
    other = {"key": "q", "label": "Q", "type": "text"}
    client.put(f"/v1/datasets/{dataset_id}/schema", json={"columns": [key_col, other]})
    client.put(f"/v1/datasets/{dataset_id}/schema", json={"columns": [other]})

    got = client.get(f"/v1/datasets/{dataset_id}/schema?include_archived=true").json()
    archived = next(c for c in got["columns"] if c["key"] == "id")
    assert archived["archived"] is True
    assert archived["is_key"] is False


def test_db_allows_at_most_one_active_key_column(dataset_id):
    db = SessionLocal()
    try:
        ds = uuid.UUID(dataset_id)
        db.add(DatasetColumn(dataset_id=ds, key="a", label="A", type=ColumnType.TEXT, is_key=True))
        db.add(DatasetColumn(dataset_id=ds, key="b", label="B", type=ColumnType.TEXT, is_key=True))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        # An archived key column doesn't count.
        db.add(
            DatasetColumn(
                dataset_id=ds, key="a", label="A", type=ColumnType.TEXT,
                is_key=True, archived=True,
            )
        )
        db.add(DatasetColumn(dataset_id=ds, key="b", label="B", type=ColumnType.TEXT, is_key=True))
        db.commit()
    finally:
        db.close()


def test_row_key_unique_per_dataset_only(dataset_id, second_dataset_id):
    db = SessionLocal()
    try:
        first, second = uuid.UUID(dataset_id), uuid.UUID(second_dataset_id)
        db.add(DatasetRow(dataset_id=first, data={"id": "k1"}, row_key="k1"))
        db.add(DatasetRow(dataset_id=first, data={"id": "k1"}, row_key="k1"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        # Same key in two datasets, and rows without a key, are fine.
        db.add(DatasetRow(dataset_id=first, data={"id": "k1"}, row_key="k1"))
        db.add(DatasetRow(dataset_id=second, data={"id": "k1"}, row_key="k1"))
        db.add(DatasetRow(dataset_id=first, data={}, row_key=None))
        db.add(DatasetRow(dataset_id=first, data={}, row_key=None))
        db.commit()
    finally:
        db.close()
