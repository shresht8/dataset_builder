"""GL-3.5-13: schema PUT rules for dotted keys, json_schema and the key column."""

from __future__ import annotations

import uuid

import pytest
from groundline_api.db import SessionLocal
from groundline_api.models.dataset import Dataset
from groundline_api.models.row import DatasetRow
from sqlalchemy import select

KEY = {"key": "id", "label": "ID", "type": "text", "required": True, "is_key": True}
ID_PLAIN = {"key": "id", "label": "ID", "type": "text", "required": True}
OTHER = {"key": "alt", "label": "Alt", "type": "text"}


@pytest.fixture()
def dataset_id(client, as_role):
    as_role("editor")
    resp = client.post("/v1/datasets", json={"name": f"gl3513s-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 201, resp.text
    ds_id = resp.json()["id"]
    yield ds_id
    db = SessionLocal()
    try:
        ds = db.get(Dataset, uuid.UUID(ds_id))
        if ds is not None:
            db.delete(ds)
            db.commit()
    finally:
        db.close()


def _put(client, dataset_id, columns):
    return client.put(f"/v1/datasets/{dataset_id}/schema", json={"columns": columns})


def _row_keys(dataset_id) -> dict[str, str | None]:
    db = SessionLocal()
    try:
        rows = db.scalars(
            select(DatasetRow).where(DatasetRow.dataset_id == uuid.UUID(dataset_id))
        )
        return {str(r.id): r.row_key for r in rows}
    finally:
        db.close()


@pytest.mark.parametrize("key", ["a..b", ".a", "a."])
def test_dotted_key_segments_must_be_non_empty(client, dataset_id, key):
    resp = _put(client, dataset_id, [{"key": key, "label": "X", "type": "text"}])
    assert resp.status_code == 422
    assert "non-empty" in resp.json()["detail"]


def test_dotted_prefix_clash_rejected(client, dataset_id):
    columns = [
        {"key": "expected", "label": "E", "type": "json"},
        {"key": "expected.answer", "label": "A", "type": "long_text"},
    ]
    resp = _put(client, dataset_id, columns)
    assert resp.status_code == 422
    assert "clash" in resp.json()["detail"]


def test_dotted_keys_without_clash_accepted(client, dataset_id):
    columns = [
        {"key": "expected.answer", "label": "A", "type": "long_text"},
        {"key": "expected.calls", "label": "C", "type": "json"},
        {"key": "expectedly", "label": "X", "type": "text"},
    ]
    assert _put(client, dataset_id, columns).status_code == 200


def test_json_schema_only_on_json_columns(client, dataset_id):
    columns = [{"key": "t", "label": "T", "type": "text", "json_schema": {"type": "string"}}]
    resp = _put(client, dataset_id, columns)
    assert resp.status_code == 422
    assert "only allowed for json" in resp.json()["detail"]


def test_invalid_json_schema_rejected(client, dataset_id):
    columns = [{"key": "j", "label": "J", "type": "json", "json_schema": {"type": "nope"}}]
    resp = _put(client, dataset_id, columns)
    assert resp.status_code == 422
    assert "invalid json_schema" in resp.json()["detail"]


def test_remote_ref_rejected_local_ref_accepted(client, dataset_id):
    remote = {"$ref": "https://example.com/schema.json"}
    resp = _put(client, dataset_id, [{"key": "j", "label": "J", "type": "json", "json_schema": remote}])
    assert resp.status_code == 422
    assert "local $ref" in resp.json()["detail"]

    local = {"$defs": {"s": {"type": "string"}}, "items": {"$ref": "#/$defs/s"}}
    resp = _put(client, dataset_id, [{"key": "j", "label": "J", "type": "json", "json_schema": local}])
    assert resp.status_code == 200, resp.text


def test_only_one_key_column(client, dataset_id):
    resp = _put(client, dataset_id, [KEY, dict(OTHER, required=True, is_key=True)])
    assert resp.status_code == 422
    assert "only one key column" in resp.json()["detail"]


def test_key_column_must_be_text_and_required(client, dataset_id):
    resp = _put(client, dataset_id, [dict(KEY, type="long_text")])
    assert resp.status_code == 422 and "must be of type text" in resp.json()["detail"]
    resp = _put(client, dataset_id, [dict(KEY, required=False)])
    assert resp.status_code == 422 and "must be required" in resp.json()["detail"]


def test_setting_key_backfills_row_keys(client, dataset_id):
    assert _put(client, dataset_id, [ID_PLAIN, OTHER]).status_code == 200
    for value in ("a", "b"):
        resp = client.post(f"/v1/datasets/{dataset_id}/rows", json={"data": {"id": value}})
        assert resp.status_code == 201, resp.text
    assert set(_row_keys(dataset_id).values()) == {None}

    assert _put(client, dataset_id, [KEY, OTHER]).status_code == 200
    assert sorted(_row_keys(dataset_id).values()) == ["a", "b"]


def test_key_backfill_refused_on_duplicates_or_missing(client, dataset_id):
    assert _put(client, dataset_id, [ID_PLAIN, OTHER]).status_code == 200
    rows = [{"id": "dup", "alt": "x"}, {"id": "dup", "alt": "y"}, {"id": "ok", "alt": " pad "}]
    for data in rows:
        resp = client.post(f"/v1/datasets/{dataset_id}/rows", json={"data": data})
        assert resp.status_code == 201, resp.text

    resp = _put(client, dataset_id, [KEY, OTHER])
    assert resp.status_code == 422
    assert "'dup' is used by rows" in resp.json()["detail"]

    alt_key = dict(OTHER, required=True, is_key=True)
    resp = _put(client, dataset_id, [ID_PLAIN, alt_key])
    assert resp.status_code == 422
    assert "leading or trailing whitespace" in resp.json()["detail"]

    # Nothing changed: no key column, no row keys.
    columns = client.get(f"/v1/datasets/{dataset_id}/schema").json()["columns"]
    assert not any(c["is_key"] for c in columns)
    assert set(_row_keys(dataset_id).values()) == {None}


def test_key_backfill_refused_when_a_row_has_no_value(client, dataset_id):
    assert _put(client, dataset_id, [ID_PLAIN, OTHER]).status_code == 200
    client.post(f"/v1/datasets/{dataset_id}/rows", json={"data": {"id": "a", "alt": "x"}})
    client.post(f"/v1/datasets/{dataset_id}/rows", json={"data": {"id": "b"}})

    resp = _put(client, dataset_id, [ID_PLAIN, dict(OTHER, required=True, is_key=True)])
    assert resp.status_code == 422
    assert "has no value" in resp.json()["detail"]


def test_switching_key_column_rebackfills(client, dataset_id):
    # Swapped values between rows would collide halfway through a naive update.
    assert _put(client, dataset_id, [ID_PLAIN, dict(OTHER, required=True)]).status_code == 200
    client.post(f"/v1/datasets/{dataset_id}/rows", json={"data": {"id": "x", "alt": "y"}})
    client.post(f"/v1/datasets/{dataset_id}/rows", json={"data": {"id": "y", "alt": "x"}})

    assert _put(client, dataset_id, [KEY, dict(OTHER, required=True)]).status_code == 200
    assert sorted(_row_keys(dataset_id).values()) == ["x", "y"]
    resp = _put(client, dataset_id, [ID_PLAIN, dict(OTHER, required=True, is_key=True)])
    assert resp.status_code == 200, resp.text
    columns = {c["key"]: c for c in resp.json()["columns"]}
    assert columns["alt"]["is_key"] and not columns["id"]["is_key"]
    assert sorted(_row_keys(dataset_id).values()) == ["x", "y"]


def test_archiving_key_column_clears_row_keys(client, dataset_id):
    assert _put(client, dataset_id, [KEY, OTHER]).status_code == 200
    client.post(f"/v1/datasets/{dataset_id}/rows", json={"data": {"id": "a"}})
    assert list(_row_keys(dataset_id).values()) == ["a"]

    assert _put(client, dataset_id, [OTHER]).status_code == 200
    assert list(_row_keys(dataset_id).values()) == [None]
