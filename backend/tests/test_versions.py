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
from groundline_api.models.version import DatasetVersion
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


def test_list_versions_newest_first_with_metadata(client, as_role, dataset_id):
    as_role("annotator")
    _create_row(client, dataset_id, {"input": "one"})

    editor = as_role("editor")
    first = client.post(f"/v1/datasets/{dataset_id}/versions", json={"notes": "v1 notes"})
    assert first.status_code == 201, first.text
    second = client.post(f"/v1/datasets/{dataset_id}/versions", json={"notes": "v2 notes"})
    assert second.status_code == 201, second.text

    as_role("viewer")
    resp = client.get(f"/v1/datasets/{dataset_id}/versions")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [v["version"] for v in body] == [2, 1]
    assert body[0]["notes"] == "v2 notes"
    assert body[1]["notes"] == "v1 notes"
    assert body[0]["created_by_email"] == editor["email"]
    assert body[0]["content_hash"].startswith("sha256:")
    assert body[0]["row_count"] == 1


def test_list_versions_404_for_missing_dataset(client, as_role):
    as_role("viewer")
    resp = client.get(f"/v1/datasets/{uuid.uuid4()}/versions")
    assert resp.status_code == 404, resp.text


def test_manifest_round_trips_through_shared_model(client, as_role, dataset_id):
    from groundline_schema.manifest import Manifest

    as_role("annotator")
    _create_row(client, dataset_id, {"input": "hi", "score": 3})

    as_role("editor")
    cut_resp = client.post(f"/v1/datasets/{dataset_id}/versions", json={"notes": "for manifest"})
    assert cut_resp.status_code == 201, cut_resp.text
    version_body = cut_resp.json()

    as_role("viewer")
    resp = client.get(f"/v1/datasets/{dataset_id}/versions/{version_body['version']}/manifest")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/json")

    manifest = Manifest.model_validate_json(resp.text)
    assert manifest.version == version_body["version"]
    assert manifest.content_hash == version_body["content_hash"]
    assert manifest.row_count == version_body["row_count"]
    assert manifest.notes == "for manifest"


def test_manifest_404_for_missing_version(client, as_role, dataset_id):
    as_role("viewer")
    resp = client.get(f"/v1/datasets/{dataset_id}/versions/999/manifest")
    assert resp.status_code == 404, resp.text


def test_manifest_404_for_missing_dataset(client, as_role):
    as_role("viewer")
    resp = client.get(f"/v1/datasets/{uuid.uuid4()}/versions/1/manifest")
    assert resp.status_code == 404, resp.text


def test_no_endpoint_mutates_or_deletes_a_version(client, as_role, dataset_id, storage_bucket):
    as_role("annotator")
    row = _create_row(client, dataset_id, {"input": "immutable", "score": 1})

    as_role("editor")
    dataset_name = client.get(f"/v1/datasets/{dataset_id}").json()["name"]
    cut_resp = client.post(f"/v1/datasets/{dataset_id}/versions", json={"notes": "orig"})
    assert cut_resp.status_code == 201, cut_resp.text
    version_num = cut_resp.json()["version"]

    before_manifest = _get_object(storage_bucket, dataset_name, version_num, "manifest.json")
    before_rows = _get_object(storage_bucket, dataset_name, version_num, "rows.jsonl")

    db = SessionLocal()
    try:
        before_row = db.get(DatasetVersion, uuid.UUID(cut_resp.json()["id"]))
        before_hash = before_row.content_hash
        before_count = before_row.row_count
    finally:
        db.close()

    base = f"/v1/datasets/{dataset_id}/versions/{version_num}"
    for verb, path in [
        ("put", base),
        ("patch", base),
        ("delete", base),
        ("put", f"{base}/manifest"),
        ("patch", f"{base}/manifest"),
        ("delete", f"{base}/manifest"),
    ]:
        if verb == "delete":
            resp = client.delete(path)
        else:
            resp = getattr(client, verb)(path, json={"notes": "hacked"})
        assert resp.status_code in (404, 405), f"{verb} {path} -> {resp.status_code}"

    # Approve more rows and cut again; the earlier version's stored objects and
    # DB row must be untouched (reads pull from the stored snapshot, not live rows).
    as_role("annotator")
    _create_row(client, dataset_id, {"input": "added-after", "score": 2})
    as_role("editor")
    client.patch(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}",
        json={"data": {"input": "changed", "score": 99}},
        headers={"If-Match": str(row["rev"])},
    )

    after_manifest = _get_object(storage_bucket, dataset_name, version_num, "manifest.json")
    after_rows = _get_object(storage_bucket, dataset_name, version_num, "rows.jsonl")
    assert after_manifest == before_manifest
    assert after_rows == before_rows

    db = SessionLocal()
    try:
        after_row = db.get(DatasetVersion, uuid.UUID(cut_resp.json()["id"]))
        assert after_row.content_hash == before_hash
        assert after_row.row_count == before_count
    finally:
        db.close()

    as_role("viewer")
    manifest_resp = client.get(f"{base}/manifest")
    assert manifest_resp.content == before_manifest


def test_pat_can_list_versions_and_fetch_manifest(client, as_role, dataset_id):
    as_role("annotator")
    _create_row(client, dataset_id, {"input": "pat-check"})

    as_role("editor")
    cut_resp = client.post(f"/v1/datasets/{dataset_id}/versions", json={})
    assert cut_resp.status_code == 201, cut_resp.text
    version_num = cut_resp.json()["version"]

    as_role("viewer")
    raw = client.post("/v1/auth/tokens", json={"name": "gl3-3-pat"}).json()["token"]
    client.cookies.clear()

    headers = {"Authorization": f"Bearer {raw}"}
    list_resp = client.get(f"/v1/datasets/{dataset_id}/versions", headers=headers)
    assert list_resp.status_code == 200, list_resp.text
    assert any(v["version"] == version_num for v in list_resp.json())

    manifest_resp = client.get(
        f"/v1/datasets/{dataset_id}/versions/{version_num}/manifest", headers=headers
    )
    assert manifest_resp.status_code == 200, manifest_resp.text


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
