"""GL-2-3: row status transitions, assignment, and my-queue (§4)."""

from __future__ import annotations

import uuid

import pytest
from groundline_api.db import SessionLocal
from groundline_api.models.dataset import Dataset


@pytest.fixture()
def dataset_id(client, as_role):
    as_role("editor")  # dataset writes are editor-only (§5)
    resp = client.post("/v1/datasets", json={"name": f"gl23-{uuid.uuid4().hex[:8]}"})
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
    resp = client.post(f"/v1/datasets/{dataset_id}/rows", json={"data": {"input": "hi"}})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_default_status_draft_no_assignee(row):
    assert row["status"] == "draft"
    assert row["assignee"] is None


def test_status_change_persists_and_is_filterable(client, as_role, dataset_id, row):
    as_role("annotator")
    resp = client.patch(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}",
        json={"status": "needs_review"},
        headers={"If-Match": str(row["rev"])},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "needs_review"
    assert body["rev"] == row["rev"] + 1

    listed = client.get(f"/v1/datasets/{dataset_id}/rows", params={"status": "needs_review"})
    assert listed.status_code == 200, listed.text
    assert [r["id"] for r in listed.json()] == [row["id"]]


def test_status_moves_in_any_direction(client, as_role, dataset_id, row):
    as_role("annotator")
    rev = row["rev"]
    for target in ["needs_review", "approved", "draft"]:
        resp = client.patch(
            f"/v1/datasets/{dataset_id}/rows/{row['id']}",
            json={"status": target},
            headers={"If-Match": str(rev)},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == target
        rev = resp.json()["rev"]


def test_invalid_status_422(client, as_role, dataset_id, row):
    as_role("annotator")
    resp = client.patch(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}",
        json={"status": "bogus"},
        headers={"If-Match": str(row["rev"])},
    )
    assert resp.status_code == 422, resp.text


def test_assign_then_unassign(client, as_role, dataset_id, row):
    annotator = as_role("annotator")
    resp = client.patch(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}",
        json={"assignee": annotator["id"]},
        headers={"If-Match": str(row["rev"])},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["assignee"] == annotator["id"]

    resp = client.patch(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}",
        json={"assignee": None},
        headers={"If-Match": str(body["rev"])},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["assignee"] is None


def test_assignee_must_reference_existing_user_422(client, as_role, dataset_id, row):
    as_role("annotator")
    resp = client.patch(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}",
        json={"assignee": str(uuid.uuid4())},
        headers={"If-Match": str(row["rev"])},
    )
    assert resp.status_code == 422, resp.text


def test_assignee_me_resolves_to_caller(client, as_role, dataset_id):
    editor = as_role("editor")
    resp = client.post(f"/v1/datasets/{dataset_id}/rows", json={"data": {"input": "mine"}})
    assert resp.status_code == 201, resp.text
    mine = resp.json()
    resp = client.post(f"/v1/datasets/{dataset_id}/rows", json={"data": {"input": "not mine"}})
    assert resp.status_code == 201, resp.text
    other = resp.json()

    annotator = as_role("annotator")
    resp = client.patch(
        f"/v1/datasets/{dataset_id}/rows/{mine['id']}",
        json={"assignee": annotator["id"]},
        headers={"If-Match": str(mine["rev"])},
    )
    assert resp.status_code == 200, resp.text

    as_role("editor")
    resp = client.patch(
        f"/v1/datasets/{dataset_id}/rows/{other['id']}",
        json={"assignee": editor["id"]},
        headers={"If-Match": str(other["rev"])},
    )
    assert resp.status_code == 200, resp.text

    as_role("annotator")
    resp = client.get(f"/v1/datasets/{dataset_id}/rows", params={"assignee": "me"})
    assert resp.status_code == 200, resp.text
    ids = {r["id"] for r in resp.json()}
    assert ids == {mine["id"]}


def test_assignee_literal_uuid_still_works(client, as_role, dataset_id, row):
    annotator = as_role("annotator")
    resp = client.patch(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}",
        json={"assignee": annotator["id"]},
        headers={"If-Match": str(row["rev"])},
    )
    assert resp.status_code == 200, resp.text

    resp = client.get(
        f"/v1/datasets/{dataset_id}/rows", params={"assignee": annotator["id"]}
    )
    assert resp.status_code == 200, resp.text
    assert [r["id"] for r in resp.json()] == [row["id"]]


def test_status_and_assignee_share_rev_locking(client, as_role, dataset_id, row):
    as_role("annotator")
    resp = client.patch(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}",
        json={"status": "approved"},
        headers={"If-Match": "1"},
    )
    assert resp.status_code == 200, resp.text

    # The rev already moved to 2; retrying with the stale rev conflicts (§4).
    resp = client.patch(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}",
        json={"status": "draft"},
        headers={"If-Match": "1"},
    )
    assert resp.status_code == 409, resp.text


def test_annotator_can_change_status_and_assignment(client, as_role, dataset_id, row):
    annotator = as_role("annotator")
    resp = client.patch(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}",
        json={"status": "needs_review", "assignee": annotator["id"]},
        headers={"If-Match": str(row["rev"])},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "needs_review"
    assert body["assignee"] == annotator["id"]


def test_viewer_cannot_change_status_403(client, as_role, dataset_id, row):
    as_role("viewer")
    resp = client.patch(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}",
        json={"status": "approved"},
        headers={"If-Match": str(row["rev"])},
    )
    assert resp.status_code == 403, resp.text
