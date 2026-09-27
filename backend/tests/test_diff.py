"""GL-3-6: version diff endpoint (§6, C3).

Storage tests use their own per-run bucket (Rules for every agent).
"""

from __future__ import annotations

import uuid

import boto3
import pytest
from groundline_api.config import settings
from groundline_api.db import SessionLocal
from groundline_api.models.dataset import Dataset

COLUMNS = [
    {"key": "input", "label": "Input", "type": "text", "required": True},
    {"key": "score", "label": "Score", "type": "number"},
]


@pytest.fixture(scope="module")
def storage_bucket():
    bucket = f"gl3-6-test-{uuid.uuid4().hex[:8]}"
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
    resp = client.post("/v1/datasets", json={"name": f"gl36-{uuid.uuid4().hex[:8]}"})
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


def _cut(client, dataset_id):
    resp = client.post(f"/v1/datasets/{dataset_id}/versions", json={})
    assert resp.status_code == 201, resp.text
    return resp.json()["version"]


def test_added_removed_modified_with_field_detail(client, as_role, dataset_id):
    as_role("annotator")
    stays = _create_row(client, dataset_id, {"input": "unchanged", "score": 1})
    to_remove = _create_row(client, dataset_id, {"input": "gone", "score": 2})
    to_modify = _create_row(client, dataset_id, {"input": "before", "score": 3})

    as_role("editor")
    v1 = _cut(client, dataset_id)

    # Un-approve `to_remove` so it drops out of the (approved-only) v2 cut.
    client.patch(
        f"/v1/datasets/{dataset_id}/rows/{to_remove['id']}",
        json={"status": "needs_review"},
        headers={"If-Match": str(to_remove["rev"])},
    )
    client.patch(
        f"/v1/datasets/{dataset_id}/rows/{to_modify['id']}",
        json={"data": {"input": "after", "score": 9}},
        headers={"If-Match": str(to_modify["rev"])},
    )
    as_role("annotator")
    added = _create_row(client, dataset_id, {"input": "new", "score": 4})

    as_role("editor")
    v2 = _cut(client, dataset_id)

    as_role("viewer")
    resp = client.get(f"/v1/datasets/{dataset_id}/versions/diff?from={v1}&to={v2}")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["from"] == v1
    assert body["to"] == v2
    assert [r["id"] for r in body["added"]] == [added["id"]]
    assert body["added"][0]["data"] == {"input": "new", "score": 4}

    assert [r["id"] for r in body["removed"]] == [to_remove["id"]]
    assert body["removed"][0]["data"] == {"input": "gone", "score": 2}

    modified = {m["id"]: m for m in body["modified"]}
    assert to_modify["id"] in modified
    assert stays["id"] not in modified
    mod = modified[to_modify["id"]]
    assert mod["status"] is None
    changes = {c["field"]: c for c in mod["changes"]}
    assert changes["input"] == {"field": "input", "old": "before", "new": "after"}
    assert changes["score"] == {"field": "score", "old": 3, "new": 9}


def test_status_only_change_shows_as_modified(client, as_role, dataset_id):
    as_role("annotator")
    row = _create_row(client, dataset_id, {"input": "x", "score": 1}, approve=False)

    as_role("editor")
    v1 = client.post(f"/v1/datasets/{dataset_id}/versions", json={"include_unapproved": True})
    v1 = v1.json()["version"]

    as_role("editor")
    client.patch(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}",
        json={"status": "approved"},
        headers={"If-Match": str(row["rev"])},
    )
    v2 = client.post(f"/v1/datasets/{dataset_id}/versions", json={"include_unapproved": True})
    v2 = v2.json()["version"]

    as_role("viewer")
    resp = client.get(f"/v1/datasets/{dataset_id}/versions/diff?from={v1}&to={v2}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    modified = {m["id"]: m for m in body["modified"]}
    assert row["id"] in modified
    mod = modified[row["id"]]
    assert mod["status"] == {"old": "draft", "new": "approved"}
    assert mod["changes"] == []
    assert body["added"] == []
    assert body["removed"] == []


def test_identical_versions_give_empty_diff(client, as_role, dataset_id):
    as_role("annotator")
    _create_row(client, dataset_id, {"input": "stable", "score": 1})

    as_role("editor")
    v1 = _cut(client, dataset_id)
    v2 = _cut(client, dataset_id)  # same approved rows, unchanged

    as_role("viewer")
    resp = client.get(f"/v1/datasets/{dataset_id}/versions/diff?from={v1}&to={v2}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["added"] == []
    assert body["removed"] == []
    assert body["modified"] == []
    assert body["schema_changes"] == {"added": [], "removed": [], "changed": []}


def test_from_equals_to_gives_empty_diff(client, as_role, dataset_id):
    as_role("annotator")
    _create_row(client, dataset_id, {"input": "x", "score": 1})

    as_role("editor")
    v1 = _cut(client, dataset_id)

    as_role("viewer")
    resp = client.get(f"/v1/datasets/{dataset_id}/versions/diff?from={v1}&to={v1}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["from"] == v1
    assert body["to"] == v1
    assert body["added"] == []
    assert body["removed"] == []
    assert body["modified"] == []


def test_schema_change_column_added_between_cuts(client, as_role, dataset_id):
    as_role("annotator")
    _create_row(client, dataset_id, {"input": "x", "score": 1})

    as_role("editor")
    v1 = _cut(client, dataset_id)

    resp = client.put(
        f"/v1/datasets/{dataset_id}/schema",
        json={"columns": COLUMNS + [{"key": "note", "label": "Note", "type": "text"}]},
    )
    assert resp.status_code == 200, resp.text

    as_role("annotator")
    _create_row(client, dataset_id, {"input": "y", "score": 2, "note": "hi"})

    as_role("editor")
    v2 = _cut(client, dataset_id)

    as_role("viewer")
    resp = client.get(f"/v1/datasets/{dataset_id}/versions/diff?from={v1}&to={v2}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["schema_changes"] == {"added": ["note"], "removed": [], "changed": []}
    # The new row's `note` field appears in `added` data, not as a per-row change.
    assert body["added"][0]["data"]["note"] == "hi"


def test_diff_404_for_missing_dataset(client, as_role):
    as_role("viewer")
    resp = client.get(f"/v1/datasets/{uuid.uuid4()}/versions/diff?from=1&to=2")
    assert resp.status_code == 404, resp.text


def test_diff_404_for_missing_version(client, as_role, dataset_id):
    as_role("annotator")
    _create_row(client, dataset_id, {"input": "x", "score": 1})
    as_role("editor")
    v1 = _cut(client, dataset_id)

    as_role("viewer")
    resp = client.get(f"/v1/datasets/{dataset_id}/versions/diff?from={v1}&to=999")
    assert resp.status_code == 404, resp.text
    resp = client.get(f"/v1/datasets/{dataset_id}/versions/diff?from=999&to={v1}")
    assert resp.status_code == 404, resp.text


def test_diff_route_is_not_shadowed_by_the_v_int_route(client, as_role, dataset_id):
    """Route-order regression: /versions/diff must not 422 as /versions/{v}."""
    as_role("annotator")
    _create_row(client, dataset_id, {"input": "x", "score": 1})
    as_role("editor")
    _cut(client, dataset_id)
    _cut(client, dataset_id)

    as_role("viewer")
    resp = client.get(f"/v1/datasets/{dataset_id}/versions/diff?from=1&to=2")
    assert resp.status_code == 200, resp.text
