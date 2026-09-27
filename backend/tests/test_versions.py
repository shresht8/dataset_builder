"""GL-3-2: cutting a version (§6, C2/C3).

Storage tests use their own per-run bucket (created and torn down here), not
the bucket named in `.env`.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import uuid

import boto3
import pytest
from groundline_api.config import settings
from groundline_api.db import SessionLocal
from groundline_api.models.dataset import Dataset
from groundline_api.models.user import User
from groundline_api.services.versioning import EmptySelectionError, cut_version

COLUMNS = [
    {"key": "input", "label": "Input", "type": "text", "required": True},
    {"key": "score", "label": "Score", "type": "number"},
]


@pytest.fixture(scope="module")
def storage_bucket():
    """A throwaway bucket for this test module only (Rules for every agent)."""
    bucket = f"gl3-2-test-{uuid.uuid4().hex[:8]}"
    client = boto3.client(
        "s3",
        endpoint_url=settings.storage_endpoint_url,
        aws_access_key_id=settings.storage_access_key,
        aws_secret_access_key=settings.storage_secret_key,
    )
    client.create_bucket(Bucket=bucket)
    original_bucket = settings.storage_bucket
    settings.storage_bucket = bucket
    yield client, bucket
    settings.storage_bucket = original_bucket
    objects = client.list_objects_v2(Bucket=bucket).get("Contents", [])
    for obj in objects:
        client.delete_object(Bucket=bucket, Key=obj["Key"])
    client.delete_bucket(Bucket=bucket)


@pytest.fixture()
def dataset_id(client, as_role, storage_bucket):
    as_role("editor")
    resp = client.post("/v1/datasets", json={"name": f"gl32-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 201, resp.text
    ds_id = resp.json()["id"]
    resp = client.put(f"/v1/datasets/{ds_id}/schema", json={"columns": COLUMNS})
    assert resp.status_code == 200, resp.text
    yield ds_id
    db = SessionLocal()
    try:
        ds = db.get(Dataset, uuid.UUID(ds_id))
        if ds is not None:
            db.delete(ds)  # rows/versions cascade via FK
            db.commit()
    finally:
        db.close()


def _create_row(client, dataset_id, data, approve=True):
    resp = client.post(f"/v1/datasets/{dataset_id}/rows", json={"data": data})
    assert resp.status_code == 201, resp.text
    row = resp.json()
    if approve:
        resp = client.patch(
            f"/v1/datasets/{dataset_id}/rows/{row['id']}",
            json={"status": "approved"},
            headers={"If-Match": str(row["rev"])},
        )
        assert resp.status_code == 200, resp.text
        row = resp.json()
    return row


def _get_object(storage_bucket, dataset_id_name, version, filename):
    client, bucket = storage_bucket
    key = f"datasets/{dataset_id_name}/v{version}/{filename}"
    return client.get_object(Bucket=bucket, Key=key)["Body"].read()


def test_cut_produces_three_files_at_right_key(client, as_role, dataset_id, storage_bucket):
    as_role("annotator")
    _create_row(client, dataset_id, {"input": "hi", "score": 1})

    as_role("editor")
    dataset_name = client.get(f"/v1/datasets/{dataset_id}").json()["name"]
    resp = client.post(f"/v1/datasets/{dataset_id}/versions", json={"notes": "first cut"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["version"] == 1
    assert body["row_count"] == 1
    assert body["notes"] == "first cut"
    assert body["created_by_email"] is not None
    assert body["content_hash"].startswith("sha256:")

    rows_bytes = _get_object(storage_bucket, dataset_name, 1, "rows.jsonl")
    schema_bytes = _get_object(storage_bucket, dataset_name, 1, "schema.json")
    manifest_bytes = _get_object(storage_bucket, dataset_name, 1, "manifest.json")

    assert hashlib.sha256(rows_bytes).hexdigest() == body["content_hash"].removeprefix("sha256:")

    schema = json.loads(schema_bytes)
    assert [c["key"] for c in schema["columns"]] == ["input", "score"]

    manifest = json.loads(manifest_bytes)
    assert manifest["dataset"] == dataset_name
    assert manifest["version"] == 1
    assert manifest["content_hash"] == body["content_hash"]
    assert manifest["row_count"] == 1

    lines = rows_bytes.decode("utf-8").splitlines()
    assert len(lines) == 1
    line = json.loads(lines[0])
    assert line["status"] == "approved"
    assert line["data"] == {"input": "hi", "score": 1}


def test_deterministic_hash_across_cuts_of_the_same_rows(client, as_role, dataset_id):
    as_role("annotator")
    _create_row(client, dataset_id, {"input": "stable", "score": 5})

    as_role("editor")
    first = client.post(f"/v1/datasets/{dataset_id}/versions", json={})
    assert first.status_code == 201, first.text
    second = client.post(f"/v1/datasets/{dataset_id}/versions", json={})
    assert second.status_code == 201, second.text

    assert first.json()["content_hash"] == second.json()["content_hash"]
    assert first.json()["version"] == 1
    assert second.json()["version"] == 2


def test_versions_increment(client, as_role, dataset_id):
    as_role("annotator")
    _create_row(client, dataset_id, {"input": "a"})

    as_role("editor")
    versions = []
    for _ in range(3):
        resp = client.post(f"/v1/datasets/{dataset_id}/versions", json={})
        assert resp.status_code == 201, resp.text
        versions.append(resp.json()["version"])
    assert versions == [1, 2, 3]


def test_concurrent_cuts_dont_collide(client, as_role, dataset_id):
    as_role("annotator")
    _create_row(client, dataset_id, {"input": "concurrent"})

    editor_email = as_role("editor")["email"]
    ds_uuid = uuid.UUID(dataset_id)

    def worker():
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == editor_email).one()
            version = cut_version(db, ds_uuid, user, notes=None, include_unapproved=False)
            return version.version
        finally:
            db.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker), pool.submit(worker)]
        results = [f.result() for f in futures]

    assert sorted(results) == [1, 2]


def test_annotator_403_editor_201(client, as_role, dataset_id):
    as_role("annotator")
    _create_row(client, dataset_id, {"input": "x"})

    as_role("annotator")
    resp = client.post(f"/v1/datasets/{dataset_id}/versions", json={})
    assert resp.status_code == 403, resp.text

    as_role("editor")
    resp = client.post(f"/v1/datasets/{dataset_id}/versions", json={})
    assert resp.status_code == 201, resp.text


def test_empty_selection_422(client, as_role, dataset_id):
    as_role("annotator")
    _create_row(client, dataset_id, {"input": "draft only"}, approve=False)

    as_role("editor")
    resp = client.post(f"/v1/datasets/{dataset_id}/versions", json={})
    assert resp.status_code == 422, resp.text


def test_include_unapproved_includes_drafts_with_status(client, as_role, dataset_id):
    as_role("annotator")
    _create_row(client, dataset_id, {"input": "approved"}, approve=True)
    _create_row(client, dataset_id, {"input": "draft"}, approve=False)

    as_role("editor")
    resp = client.post(
        f"/v1/datasets/{dataset_id}/versions", json={"include_unapproved": True}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["row_count"] == 2


def test_empty_selection_does_not_leave_a_version_row(client, as_role, dataset_id):
    """EmptySelectionError must not commit a `dataset_versions` row (422 must
    stay retriable: a version can never be deleted)."""
    ds_uuid = uuid.UUID(dataset_id)
    db = SessionLocal()
    try:
        with pytest.raises(EmptySelectionError):
            cut_version(db, ds_uuid, db.query(User).first(), notes=None, include_unapproved=False)
    finally:
        db.close()
