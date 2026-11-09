"""GL-135: only valid rows can be approved or cut; editing an approved row sends it back to review."""

from __future__ import annotations

import uuid

import boto3
import pytest
from groundline_api.config import settings
from groundline_api.db import SessionLocal
from groundline_api.models.row import DatasetRow, RowStatus
from groundline_api.models.version import DatasetVersion
from sqlalchemy import select

COLUMNS = [
    {"key": "id", "label": "Id", "type": "text", "required": True, "is_key": True},
    {"key": "score", "label": "Score", "type": "number"},
    {"key": "colour", "label": "Colour", "type": "select", "options": ["red", "blue"], "required": True},
]
GOOD = {"id": "g1", "score": 1, "colour": "red"}


@pytest.fixture(scope="module")
def storage_bucket():
    bucket = f"gl135-test-{uuid.uuid4().hex[:8]}"
    s3 = boto3.client(
        "s3",
        endpoint_url=settings.storage_endpoint_url,
        aws_access_key_id=settings.storage_access_key,
        aws_secret_access_key=settings.storage_secret_key,
    )
    s3.create_bucket(Bucket=bucket)
    original = settings.storage_bucket
    settings.storage_bucket = bucket
    yield s3, bucket
    settings.storage_bucket = original
    for obj in s3.list_objects_v2(Bucket=bucket).get("Contents", []):
        s3.delete_object(Bucket=bucket, Key=obj["Key"])
    s3.delete_bucket(Bucket=bucket)


@pytest.fixture()
def ds(client, as_role, cleanup, storage_bucket):
    as_role("editor")
    resp = client.post("/v1/datasets", json={"name": f"gl135-{uuid.uuid4().hex[:8]}", "columns": COLUMNS})
    assert resp.status_code == 201, resp.text
    cleanup["datasets"].append(resp.json()["id"])
    return resp.json()["id"]


def _add(client, ds, data):
    resp = client.post(f"/v1/datasets/{ds}/rows", json={"data": data})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _legacy(ds, data, status=RowStatus.DRAFT):
    """A row that is already invalid, written straight into the table."""
    db = SessionLocal()
    try:
        row = DatasetRow(dataset_id=uuid.UUID(ds), data=data, status=status, row_key=data.get("id"))
        db.add(row)
        db.commit()
        return str(row.id)
    finally:
        db.close()


def _row(client, ds, row_id):
    return next(r for r in client.get(f"/v1/datasets/{ds}/rows").json() if r["id"] == row_id)


def _patch(client, ds, row, body):
    return client.patch(
        f"/v1/datasets/{ds}/rows/{row['id']}", json=body, headers={"If-Match": str(row["rev"])}
    )


def _edits(client, ds, row_id):
    return client.get(f"/v1/datasets/{ds}/rows/{row_id}/edits").json()


# --- approve gate -----------------------------------------------------------------------------


def test_approving_an_invalid_row_is_refused_with_the_row_and_reasons(client, ds):
    row_id = _legacy(ds, {"id": "r2", "score": "abc"})  # bad type, and no colour
    resp = _patch(client, ds, _row(client, ds, row_id), {"status": "approved"})
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert isinstance(detail, str)
    assert detail.startswith("Row 'r2' can't be approved: ")
    assert 'score: "abc" is not a number' in detail and "colour: is required" in detail
    assert _row(client, ds, row_id)["status"] == "draft"


def test_the_message_lists_three_problems_then_counts_the_rest(client, ds):
    bad = [{"key": f"c{i}", "label": f"C{i}", "type": "number", "required": True} for i in range(5)]
    cols = COLUMNS + bad
    assert client.put(f"/v1/datasets/{ds}/schema", json={"columns": cols}).status_code == 200
    row_id = _legacy(ds, {"id": "many"})
    resp = _patch(client, ds, _row(client, ds, row_id), {"status": "approved"})
    assert resp.status_code == 422
    assert resp.json()["detail"].endswith("and 3 more")  # colour + c0..c4 = 6 problems, 3 shown


def test_approving_after_the_fix_works_and_a_fixing_edit_can_carry_the_status(client, ds):
    row_id = _legacy(ds, {"id": "r3", "score": "abc", "colour": "red"})
    row = _row(client, ds, row_id)
    assert _patch(client, ds, row, {"status": "approved"}).status_code == 422
    # One request: the fix and the approval together.
    resp = _patch(client, ds, row, {"data": {"score": 5}, "status": "approved"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "approved"

    other = _legacy(ds, {"id": "r4", "score": "abc", "colour": "red"})
    fixed = _patch(client, ds, _row(client, ds, other), {"data": {"score": 6}})
    assert fixed.status_code == 200
    assert _patch(client, ds, fixed.json(), {"status": "approved"}).status_code == 200


def test_moving_away_from_approved_is_always_allowed(client, ds):
    row_id = _legacy(ds, {"id": "old", "score": "abc"}, status=RowStatus.APPROVED)
    for target in ("needs_review", "draft"):
        row = _row(client, ds, row_id)
        if row["status"] != "approved":
            row = _patch(client, ds, row, {"data": {"score": 1, "colour": "red"}, "status": "approved"}).json()
            assert row["status"] == "approved"
        assert _patch(client, ds, row, {"status": target}).status_code == 200


# --- edit demotes -------------------------------------------------------------------------------


def _approved(client, ds, data=GOOD):
    row = _add(client, ds, data)
    resp = _patch(client, ds, row, {"status": "approved"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_editing_an_approved_row_sends_it_to_review_and_records_it(client, as_role, ds):
    row = _approved(client, ds)
    as_role("annotator")  # every role demotes
    resp = _patch(client, ds, row, {"data": {"score": 2}})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "needs_review"
    fields = {e["field"]: (e["old_value"], e["new_value"]) for e in _edits(client, ds, row["id"])}
    assert fields["score"] == (1, 2)
    assert fields["status"] == ("approved", "needs_review")


def test_saving_the_same_value_leaves_it_approved(client, ds):
    row = _approved(client, ds)
    resp = _patch(client, ds, row, {"data": {"score": 1, "colour": "red"}})
    assert resp.status_code == 200 and resp.json()["status"] == "approved"
    assert not [e for e in _edits(client, ds, row["id"]) if e["field"] == "status"][1:]


def test_one_and_one_point_zero_are_different_values(client, ds):
    row = _approved(client, ds)
    resp = _patch(client, ds, row, {"data": {"score": 1.0}})
    assert resp.status_code == 200 and resp.json()["status"] == "needs_review"


def test_an_explicit_status_is_honoured(client, ds):
    row = _approved(client, ds)
    resp = _patch(client, ds, row, {"data": {"score": 2}, "status": "approved"})
    assert resp.status_code == 200 and resp.json()["status"] == "approved"
    resp = _patch(client, ds, resp.json(), {"data": {"score": 3}, "status": "draft"})
    assert resp.status_code == 200 and resp.json()["status"] == "draft"


def test_editing_a_draft_does_not_change_its_status(client, ds):
    row = _add(client, ds, GOOD)
    resp = _patch(client, ds, row, {"data": {"score": 2}})
    assert resp.json()["status"] == "draft"


# --- cut guard ----------------------------------------------------------------------------------------


def _versions(ds):
    db = SessionLocal()
    try:
        return db.scalars(select(DatasetVersion).where(DatasetVersion.dataset_id == uuid.UUID(ds))).all()
    finally:
        db.close()


def _objects(storage_bucket, ds):
    s3, bucket = storage_bucket
    return s3.list_objects_v2(Bucket=bucket, Prefix=f"datasets/{ds}/").get("Contents", [])


def test_cut_with_a_broken_approved_row_is_refused_and_leaves_nothing(client, ds, storage_bucket):
    _approved(client, ds)
    _legacy(ds, {"id": "old", "score": "abc"}, status=RowStatus.APPROVED)
    resp = client.post(f"/v1/datasets/{ds}/versions", json={})
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert isinstance(detail, str) and "Row 'old'" in detail and "is not a number" in detail
    assert "1 selected row" in detail
    assert _versions(ds) == [] and _objects(storage_bucket, ds) == []


def test_a_broken_draft_blocks_an_include_unapproved_cut_and_says_how_to_get_out(client, ds):
    _approved(client, ds)
    _legacy(ds, {"id": "draft", "score": "abc"})
    resp = client.post(f"/v1/datasets/{ds}/versions", json={"include_unapproved": True})
    assert resp.status_code == 422, resp.text
    assert "Row 'draft'" in resp.json()["detail"] and "approved rows only" in resp.json()["detail"]
    # The same dataset cuts fine when the broken draft is not selected.
    resp = client.post(f"/v1/datasets/{ds}/versions", json={})
    assert resp.status_code == 201, resp.text
    assert resp.json()["row_count"] == 1


def test_a_valid_include_unapproved_cut_still_works(client, ds):
    _approved(client, ds)
    _add(client, ds, {"id": "g2", "score": 2, "colour": "blue"})
    resp = client.post(f"/v1/datasets/{ds}/versions", json={"include_unapproved": True})
    assert resp.status_code == 201 and resp.json()["row_count"] == 2
