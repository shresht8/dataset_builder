"""GL-116: version storage by dataset ID, path in the manifest (Phase 4, D3, C2).

Uses its own per-run bucket, like test_versions.py.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

import boto3
import pytest
from groundline_api.config import settings
from groundline_api.db import SessionLocal
from groundline_api.models.version import DatasetVersion

COLUMNS = [
    {"key": "id", "label": "Id", "type": "text", "required": True, "is_key": True},
    {"key": "text", "label": "Text", "type": "text"},
]


@pytest.fixture(scope="module")
def storage_bucket():
    bucket = f"gl116-test-{uuid.uuid4().hex[:8]}"
    client = boto3.client(
        "s3",
        endpoint_url=settings.storage_endpoint_url,
        aws_access_key_id=settings.storage_access_key,
        aws_secret_access_key=settings.storage_secret_key,
    )
    client.create_bucket(Bucket=bucket)
    original = settings.storage_bucket
    settings.storage_bucket = bucket
    yield client, bucket
    settings.storage_bucket = original
    for obj in client.list_objects_v2(Bucket=bucket).get("Contents", []):
        client.delete_object(Bucket=bucket, Key=obj["Key"])
    client.delete_bucket(Bucket=bucket)


def _collection(client, cleanup):
    resp = client.post("/v1/collections", json={"name": f"v-{uuid.uuid4().hex[:8]}"})
    cleanup["collections"].append(resp.json()["id"])
    return resp.json()


def _cut(client, dataset_id) -> dict:
    resp = client.post(f"/v1/datasets/{dataset_id}/versions", json={"include_unapproved": True})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_two_trains_store_and_export_separately(client, as_role, cleanup, storage_bucket):
    _s3, bucket = storage_bucket
    as_role("editor")
    c = _collection(client, cleanup)
    ids = {}
    for folder in ("f1", "f2"):
        fid = client.post(f"/v1/collections/{c['id']}/folders", json={"name": folder}).json()["id"]
        ds = client.post(
            "/v1/datasets",
            json={"name": "train", "collection_id": c["id"], "folder_id": fid, "columns": COLUMNS},
        ).json()
        client.post(f"/v1/datasets/{ds['id']}/rows", json={"data": {"id": f"{folder}-1", "text": folder}})
        ids[folder] = ds["id"]

    for folder, dataset_id in ids.items():
        version = _cut(client, dataset_id)
        assert version["path"] == f"{c['name']}/{folder}/train"
        db = SessionLocal()
        try:
            stored = db.get(DatasetVersion, uuid.UUID(version["id"]))
            assert stored.snapshot_uri == f"s3://{bucket}/datasets/{dataset_id}/v1/"
        finally:
            db.close()

        manifest = client.get(f"/v1/datasets/{dataset_id}/versions/1/manifest").json()
        assert manifest["dataset"] == "train"
        assert manifest["dataset_id"] == dataset_id
        assert manifest["path"] == f"{c['name']}/{folder}/train"

        rows = client.get(f"/v1/datasets/{dataset_id}/versions/1").content
        assert [json.loads(line)["data"]["id"] for line in rows.splitlines()] == [f"{folder}-1"]
        assert manifest["content_hash"] == "sha256:" + hashlib.sha256(rows).hexdigest()


def test_old_name_keyed_snapshots_stay_readable(client, as_role, cleanup, storage_bucket):
    """A version stored under datasets/{name}/v{n}/ (pre-Phase 4) serves every read."""
    s3, bucket = storage_bucket
    as_role("editor")
    name = f"legacy-{uuid.uuid4().hex[:8]}"
    ds = client.post("/v1/datasets", json={"name": name, "columns": COLUMNS}).json()
    cleanup["datasets"].append(ds["id"])
    user_id = client.get("/v1/auth/me").json()["id"]
    row_ids = {"a": str(uuid.uuid4()), "b": str(uuid.uuid4())}

    db = SessionLocal()
    try:
        for v, texts in ((1, ["a"]), (2, ["a", "b"])):
            lines = [
                {"id": row_ids[t], "status": "approved", "data": {"id": t, "text": t}}
                for t in texts
            ]
            rows_bytes = "".join(
                json.dumps(line, sort_keys=True, separators=(",", ":")) + "\n" for line in lines
            ).encode()
            digest = hashlib.sha256(rows_bytes).hexdigest()
            manifest = {
                "dataset": name, "version": v, "content_hash": f"sha256:{digest}",
                "row_count": len(lines), "feature_id": None,
                "created_at": datetime.now(timezone.utc).isoformat(), "created_by": "x", "notes": "",
            }
            prefix = f"datasets/{name}/v{v}/"
            schema = {"columns": COLUMNS}
            for fname, body in (
                ("rows.jsonl", rows_bytes),
                ("manifest.json", json.dumps(manifest).encode()),
                ("schema.json", json.dumps(schema).encode()),
            ):
                s3.put_object(Bucket=bucket, Key=prefix + fname, Body=body)
            db.add(
                DatasetVersion(
                    dataset_id=uuid.UUID(ds["id"]), version=v,
                    snapshot_uri=f"s3://{bucket}/{prefix}", content_hash=digest,
                    schema_snapshot=schema, row_count=len(lines), created_by=uuid.UUID(user_id),
                )
            )
        db.commit()
    finally:
        db.close()

    as_role("viewer")
    base = f"/v1/datasets/{ds['id']}/versions"
    manifest = client.get(f"{base}/2/manifest")
    assert manifest.status_code == 200 and manifest.json()["dataset"] == name
    rows = client.get(f"{base}/2")
    assert "sha256:" + hashlib.sha256(rows.content).hexdigest() == manifest.json()["content_hash"]
    for fmt in ("json", "yaml"):
        assert client.get(f"{base}/2?format={fmt}&shape=nested").status_code == 200
    assert client.get(f"{base}/2/jsonschema").status_code == 200
    diff = client.get(f"{base}/diff?from=1&to=2")
    assert diff.status_code == 200, diff.text
    assert len(diff.json()["added"]) == 1
