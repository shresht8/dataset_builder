"""GL-3.5-18: soft-delete rows and delete datasets without versions."""

from __future__ import annotations

import json
import uuid

import boto3
import pytest
from groundline_api.config import settings
from groundline_api.db import SessionLocal
from groundline_api.models.dataset import Dataset
from groundline_api.models.row import DatasetRow

COLUMNS = [
    {"key": "id", "label": "ID", "type": "text", "required": True, "is_key": True},
    {"key": "q", "label": "Q", "type": "text"},
]


@pytest.fixture(scope="module")
def storage_bucket():
    bucket = f"gl3518-test-{uuid.uuid4().hex[:8]}"
    s3 = boto3.client(
        "s3",
        endpoint_url=settings.storage_endpoint_url,
        aws_access_key_id=settings.storage_access_key,
        aws_secret_access_key=settings.storage_secret_key,
    )
    s3.create_bucket(Bucket=bucket)
    original = settings.storage_bucket
    settings.storage_bucket = bucket
    yield bucket
    settings.storage_bucket = original
    for obj in s3.list_objects_v2(Bucket=bucket).get("Contents", []):
        s3.delete_object(Bucket=bucket, Key=obj["Key"])
    s3.delete_bucket(Bucket=bucket)


@pytest.fixture()
def datasets():
    created: list[str] = []
    yield created
    db = SessionLocal()
    try:
        for ds_id in created:
            ds = db.get(Dataset, uuid.UUID(ds_id))
            if ds is not None:
                db.delete(ds)
        db.commit()
    finally:
        db.close()


def _dataset(client, as_role, datasets, name=None) -> str:
    as_role("editor")
    resp = client.post("/v1/datasets", json={"name": name or f"gl3518-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 201, resp.text
    ds_id = resp.json()["id"]
    datasets.append(ds_id)
    assert client.put(f"/v1/datasets/{ds_id}/schema", json={"columns": COLUMNS}).status_code == 200
    return ds_id


@pytest.fixture()
def ds(client, as_role, datasets):
    ds_id = _dataset(client, as_role, datasets)
    for key in ("a", "b", "c"):
        assert client.post(f"/v1/datasets/{ds_id}/rows", json={"data": {"id": key}}).status_code == 201
    return ds_id


def _rows(client, ds_id) -> dict[str, dict]:
    return {r["data"]["id"]: r for r in client.get(f"/v1/datasets/{ds_id}/rows").json()}


def _delete(client, ds_id, row):
    return client.delete(f"/v1/datasets/{ds_id}/rows/{row['id']}", headers={"If-Match": str(row["rev"])})


def test_soft_delete_hides_the_row_everywhere(client, ds, storage_bucket):
    row = _rows(client, ds)["a"]
    assert _delete(client, ds, row).status_code == 204

    assert set(_rows(client, ds)) == {"b", "c"}
    base = f"/v1/datasets/{ds}/rows/{row['id']}"
    assert client.patch(base, json={"data": {"q": "x"}}, headers={"If-Match": "2"}).status_code == 404
    assert client.get(f"{base}/comments").status_code == 404
    assert client.get(f"{base}/edits").status_code == 404
    assert _delete(client, ds, dict(row, rev=2)).status_code == 404  # already deleted

    version = client.post(f"/v1/datasets/{ds}/versions", json={"include_unapproved": True}).json()
    assert version["row_count"] == 2

    db = SessionLocal()
    try:
        stored = db.get(DatasetRow, uuid.UUID(row["id"]))
        assert stored.deleted_at is not None and stored.data == {"id": "a"}  # data kept
        assert stored.rev == row["rev"] + 1
    finally:
        db.close()


def test_delete_is_audited_and_locked(client, as_role, ds):
    row = _rows(client, ds)["b"]
    resp = _delete(client, ds, dict(row, rev=row["rev"] + 5))
    assert resp.status_code == 409
    assert resp.json()["rev"] == row["rev"]
    assert client.delete(f"/v1/datasets/{ds}/rows/{row['id']}").status_code == 428

    as_role("annotator")
    assert _delete(client, ds, row).status_code == 403
    as_role("editor")
    assert _delete(client, ds, row).status_code == 204

    db = SessionLocal()
    try:
        from groundline_api.models.row import RowEdit

        edits = db.query(RowEdit).filter(RowEdit.row_id == uuid.UUID(row["id"])).all()
        assert [(e.field, e.old_value, e.new_value) for e in edits] == [("deleted", False, True)]
    finally:
        db.close()


def test_deleted_key_can_be_reused_by_create_and_import(client, ds):
    _delete(client, ds, _rows(client, ds)["a"])
    assert client.post(f"/v1/datasets/{ds}/rows", json={"data": {"id": "a"}}).status_code == 201

    _delete(client, ds, _rows(client, ds)["b"])
    resp = client.post(
        f"/v1/datasets/{ds}/import",
        files={"file": ("x.json", json.dumps([{"id": "b"}, {"id": "c"}]).encode())},
        data={"mapping": json.dumps({"id": "id"})},
    ).json()
    assert resp["imported"] == 1  # b reused; c still exists
    assert "already exists" in resp["errors"][0]["reason"]


def test_bulk_delete_by_ids_and_keys_is_all_or_nothing(client, as_role, ds):
    rows = _rows(client, ds)
    resp = client.post(f"/v1/datasets/{ds}/rows/delete", json={"keys": ["a", "nope"]})
    assert resp.json() == {"deleted": 0, "not_found": ["nope"]}
    assert set(_rows(client, ds)) == {"a", "b", "c"}

    resp = client.post(f"/v1/datasets/{ds}/rows/delete", json={"keys": ["a", "b"]})
    assert resp.json() == {"deleted": 2, "not_found": []}
    resp = client.post(f"/v1/datasets/{ds}/rows/delete", json={"ids": [rows["c"]["id"], str(uuid.uuid4())]})
    assert resp.json()["deleted"] == 0 and len(resp.json()["not_found"]) == 1
    resp = client.post(f"/v1/datasets/{ds}/rows/delete", json={"ids": [rows["c"]["id"]]})
    assert resp.json() == {"deleted": 1, "not_found": []}
    assert _rows(client, ds) == {}

    assert client.post(f"/v1/datasets/{ds}/rows/delete", json={}).status_code == 422
    as_role("annotator")
    assert client.post(f"/v1/datasets/{ds}/rows/delete", json={"keys": ["x"]}).status_code == 403


def _push(client, ds_id, records, **form):
    data = {k: json.dumps(v) if isinstance(v, dict) else str(v).lower() for k, v in form.items()}
    resp = client.post(
        f"/v1/datasets/{ds_id}/sync", files={"file": ("f.json", json.dumps(records).encode())}, data=data
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_sync_skips_pulled_then_deleted_keys(client, ds):
    revs = {key: row["rev"] for key, row in _rows(client, ds).items()}
    _delete(client, ds, _rows(client, ds)["a"])

    result = _push(client, ds, [{"id": "a"}, {"id": "new"}], revs=revs)
    assert result["applied"] is True
    assert result["deleted_keys"] == ["a"]
    assert result["created"] == 1  # "new"; "a" not recreated
    assert set(_rows(client, ds)) == {"b", "c", "new"}

    result = _push(client, ds, [{"id": "a"}], revs=revs, force=True)
    assert result["created"] == 1 and result["deleted_keys"] == []
    assert "a" in _rows(client, ds)

    # A key never pulled (no rev) is simply new.
    _delete(client, ds, _rows(client, ds)["b"])
    result = _push(client, ds, [{"id": "b"}])
    assert result["created"] == 1 and result["deleted_keys"] == []


def test_delete_dataset_without_versions_frees_the_name(client, as_role, datasets, storage_bucket):
    name = f"gl3518-name-{uuid.uuid4().hex[:6]}"
    ds_id = _dataset(client, as_role, datasets, name)
    client.post(f"/v1/datasets/{ds_id}/rows", json={"data": {"id": "a"}})

    as_role("annotator")
    assert client.delete(f"/v1/datasets/{ds_id}").status_code == 403
    as_role("editor")
    assert client.delete(f"/v1/datasets/{ds_id}").status_code == 204
    assert client.get(f"/v1/datasets/{ds_id}").status_code == 404
    again = _dataset(client, as_role, datasets, name)  # name reusable

    client.post(f"/v1/datasets/{again}/rows", json={"data": {"id": "a"}})
    assert client.post(f"/v1/datasets/{again}/versions", json={"include_unapproved": True}).status_code == 201
    resp = client.delete(f"/v1/datasets/{again}")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "dataset has 1 version(s) and cannot be deleted"
    assert client.get(f"/v1/datasets/{again}").status_code == 200
