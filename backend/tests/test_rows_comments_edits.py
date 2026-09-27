"""GL-2-4: row comments and the append-only row_edits audit (§3, §4)."""

from __future__ import annotations

import uuid

import pytest
from groundline_api.db import SessionLocal
from groundline_api.models.dataset import Dataset


@pytest.fixture()
def dataset_id(client, as_role):
    as_role("editor")  # dataset writes are editor-only (§5)
    resp = client.post("/v1/datasets", json={"name": f"gl24-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 201, resp.text
    ds_id = resp.json()["id"]
    yield ds_id
    db = SessionLocal()
    try:
        ds = db.get(Dataset, uuid.UUID(ds_id))
        if ds is not None:
            db.delete(ds)  # rows cascade via FK
            db.commit()
    finally:
        db.close()


@pytest.fixture()
def row(client, as_role, dataset_id):
    as_role("annotator")
    resp = client.post(
        f"/v1/datasets/{dataset_id}/rows", json={"data": {"input": "hi", "n": 1}}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_post_and_get_comments_ordered_by_time(client, as_role, dataset_id, row):
    as_role("annotator")
    first = client.post(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}/comments",
        json={"body": "why is this marked incorrect?"},
    )
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["body"] == "why is this marked incorrect?"
    assert body["row_id"] == row["id"]
    assert body["user_id"] is not None
    assert body["created_at"] is not None

    second = client.post(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}/comments",
        json={"body": "the cited clause doesn't match"},
    )
    assert second.status_code == 201, second.text

    as_role("viewer")
    listed = client.get(f"/v1/datasets/{dataset_id}/rows/{row['id']}/comments")
    assert listed.status_code == 200, listed.text
    bodies = [c["body"] for c in listed.json()]
    assert bodies == ["why is this marked incorrect?", "the cited clause doesn't match"]


def test_empty_comment_body_422(client, as_role, dataset_id, row):
    as_role("annotator")
    resp = client.post(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}/comments", json={"body": ""}
    )
    assert resp.status_code == 422, resp.text


def test_viewer_cannot_post_comment_403(client, as_role, dataset_id, row):
    as_role("viewer")
    resp = client.post(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}/comments", json={"body": "hi"}
    )
    assert resp.status_code == 403, resp.text


def test_comment_row_mismatch_404(client, as_role, dataset_id, row):
    as_role("editor")
    other_ds = client.post("/v1/datasets", json={"name": f"gl24-other-{uuid.uuid4().hex[:8]}"})
    assert other_ds.status_code == 201, other_ds.text
    other_ds_id = other_ds.json()["id"]

    as_role("annotator")
    resp = client.post(
        f"/v1/datasets/{other_ds_id}/rows/{row['id']}/comments", json={"body": "hi"}
    )
    assert resp.status_code == 404, resp.text

    db = SessionLocal()
    try:
        ds = db.get(Dataset, uuid.UUID(other_ds_id))
        if ds is not None:
            db.delete(ds)
            db.commit()
    finally:
        db.close()


def test_patch_two_field_changes_writes_two_edits(client, as_role, dataset_id, row):
    as_role("annotator")
    resp = client.patch(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}",
        json={"data": {"n": 2}, "status": "needs_review"},
        headers={"If-Match": str(row["rev"])},
    )
    assert resp.status_code == 200, resp.text

    edits = client.get(f"/v1/datasets/{dataset_id}/rows/{row['id']}/edits")
    assert edits.status_code == 200, edits.text
    records = edits.json()
    assert len(records) == 2
    by_field = {r["field"]: r for r in records}
    assert by_field["n"]["old_value"] == 1
    assert by_field["n"]["new_value"] == 2
    assert by_field["status"]["old_value"] == "draft"
    assert by_field["status"]["new_value"] == "needs_review"
    assert all(r["user_id"] is not None for r in records)
    # ordered by time
    assert [r["at"] for r in records] == sorted(r["at"] for r in records)


def test_patch_with_identical_value_writes_no_edit(client, as_role, dataset_id, row):
    as_role("annotator")
    resp = client.patch(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}",
        json={"data": {"n": 1}},  # same as existing value
        headers={"If-Match": str(row["rev"])},
    )
    assert resp.status_code == 200, resp.text

    edits = client.get(f"/v1/datasets/{dataset_id}/rows/{row['id']}/edits")
    assert edits.status_code == 200, edits.text
    assert edits.json() == []


def test_patch_assignee_change_writes_edit(client, as_role, dataset_id, row):
    annotator = as_role("annotator")
    resp = client.patch(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}",
        json={"assignee": annotator["id"]},
        headers={"If-Match": str(row["rev"])},
    )
    assert resp.status_code == 200, resp.text

    edits = client.get(f"/v1/datasets/{dataset_id}/rows/{row['id']}/edits")
    assert edits.status_code == 200, edits.text
    records = edits.json()
    assert len(records) == 1
    assert records[0]["field"] == "assignee"
    assert records[0]["old_value"] is None
    assert records[0]["new_value"] == annotator["id"]


def test_edits_readable_by_viewer(client, as_role, dataset_id, row):
    as_role("annotator")
    resp = client.patch(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}",
        json={"status": "approved"},
        headers={"If-Match": str(row["rev"])},
    )
    assert resp.status_code == 200, resp.text

    as_role("viewer")
    edits = client.get(f"/v1/datasets/{dataset_id}/rows/{row['id']}/edits")
    assert edits.status_code == 200, edits.text
    assert len(edits.json()) == 1
