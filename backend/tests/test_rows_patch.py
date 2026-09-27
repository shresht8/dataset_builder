"""GL-2-2: optimistic locking on PATCH /v1/datasets/{id}/rows/{rid} (§4)."""

from __future__ import annotations

import uuid

import pytest
from groundline_api.db import SessionLocal
from groundline_api.models.dataset import Dataset


@pytest.fixture()
def dataset_id(client, as_role):
    as_role("editor")  # dataset writes are editor-only (§5)
    resp = client.post("/v1/datasets", json={"name": f"gl22-{uuid.uuid4().hex[:8]}"})
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


def test_patch_includes_rev_and_bumps_by_one(client, as_role, dataset_id, row):
    as_role("annotator")
    assert row["rev"] == 1
    resp = client.patch(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}",
        json={"data": {"n": 2}},
        headers={"If-Match": "1"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rev"] == 2
    assert body["data"] == {"input": "hi", "n": 2}  # only "n" changed
    assert body["updated_by"] is not None


def test_second_patch_on_stale_rev_conflicts_409(client, as_role, dataset_id, row):
    as_role("annotator")
    first = client.patch(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}",
        json={"data": {"n": 2}},
        headers={"If-Match": "1"},
    )
    assert first.status_code == 200, first.text
    assert first.json()["rev"] == 2

    # Second PATCH still carries the original (now stale) rev.
    second = client.patch(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}",
        json={"data": {"n": 3}},
        headers={"If-Match": "1"},
    )
    assert second.status_code == 409, second.text
    conflict_body = second.json()
    assert conflict_body["rev"] == 2
    assert conflict_body["data"] == {"input": "hi", "n": 2}  # current state, unaffected

    # The row was not mutated by the losing PATCH.
    listed = client.get(f"/v1/datasets/{dataset_id}/rows")
    assert listed.status_code == 200
    current = next(r for r in listed.json() if r["id"] == row["id"])
    assert current["rev"] == 2
    assert current["data"]["n"] == 2


def test_missing_if_match_428(client, as_role, dataset_id, row):
    as_role("annotator")
    resp = client.patch(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}", json={"data": {"n": 2}}
    )
    assert resp.status_code == 428, resp.text


def test_garbage_if_match_400(client, as_role, dataset_id, row):
    as_role("annotator")
    resp = client.patch(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}",
        json={"data": {"n": 2}},
        headers={"If-Match": "not-a-rev"},
    )
    assert resp.status_code == 400, resp.text


def test_viewer_cannot_patch_403(client, as_role, dataset_id, row):
    as_role("viewer")
    resp = client.patch(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}",
        json={"data": {"n": 2}},
        headers={"If-Match": "1"},
    )
    assert resp.status_code == 403, resp.text


def test_row_not_found_404(client, as_role, dataset_id):
    as_role("annotator")
    missing = uuid.uuid4()
    resp = client.patch(
        f"/v1/datasets/{dataset_id}/rows/{missing}",
        json={"data": {"n": 2}},
        headers={"If-Match": "1"},
    )
    assert resp.status_code == 404, resp.text


def test_partial_patch_validated_against_merged_result(client, as_role, dataset_id):
    as_role("editor")
    resp = client.put(
        f"/v1/datasets/{dataset_id}/schema",
        json={
            "columns": [
                {"key": "input", "label": "Input", "type": "text", "required": True},
                {"key": "score", "label": "Score", "type": "number"},
            ]
        },
    )
    assert resp.status_code == 200, resp.text

    as_role("annotator")
    resp = client.post(
        f"/v1/datasets/{dataset_id}/rows", json={"data": {"input": "hi", "score": 1}}
    )
    assert resp.status_code == 201, resp.text
    row = resp.json()

    # Untouched keys survive the merge and still satisfy "required".
    resp = client.patch(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}",
        json={"data": {"score": 5}},
        headers={"If-Match": "1"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == {"input": "hi", "score": 5}

    # Clearing a required field via partial update still fails validation.
    resp = client.patch(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}",
        json={"data": {"input": ""}},
        headers={"If-Match": "2"},
    )
    assert resp.status_code == 422, resp.text
    assert "column 'input'" in resp.json()["detail"]
