"""GL-2-1: row create typed validation + list filters (§3, §4, §8)."""

from __future__ import annotations

import uuid

import pytest
from groundline_api.db import SessionLocal
from groundline_api.models.dataset import Dataset
from groundline_api.models.row import DatasetRow, RowStatus
from groundline_api.models.user import User


SCHEMA_COLUMNS = [
    {"key": "input", "label": "Customer question", "type": "long_text", "required": True},
    {
        "key": "verdict",
        "label": "Correct?",
        "type": "select",
        "options": ["correct", "partially_correct", "incorrect"],
        "required": True,
    },
    {
        "key": "tags",
        "label": "Tags",
        "type": "multi_select",
        "options": ["billing", "eligibility", "fraud"],
    },
    {"key": "score", "label": "Score", "type": "number"},
    {"key": "flagged", "label": "Flagged", "type": "boolean"},
    {"key": "notes", "label": "Notes", "type": "text"},
]

VALID_DATA = {
    "input": "Is the customer eligible?",
    "verdict": "correct",
    "tags": ["billing", "eligibility"],
    "score": 4.5,
    "flagged": True,
    "notes": "looks fine",
}


@pytest.fixture()
def dataset_id(client, as_role):
    as_role("editor")  # dataset + schema writes are editor-only (§5)
    resp = client.post("/v1/datasets", json={"name": f"gl21-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 201, resp.text
    ds_id = resp.json()["id"]
    resp = client.put(f"/v1/datasets/{ds_id}/schema", json={"columns": SCHEMA_COLUMNS})
    assert resp.status_code == 200, resp.text
    yield ds_id
    db = SessionLocal()
    try:
        ds = db.get(Dataset, uuid.UUID(ds_id))
        if ds is not None:
            db.delete(ds)  # columns + rows cascade via FK
            db.commit()
    finally:
        db.close()


def test_valid_row_persists(client, as_role, dataset_id):
    as_role("annotator")
    resp = client.post(f"/v1/datasets/{dataset_id}/rows", json={"data": VALID_DATA})
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"] == VALID_DATA


@pytest.mark.parametrize(
    "overrides, expected_column",
    [
        ({"score": "not-a-number"}, "score"),
        ({"flagged": "yes"}, "flagged"),
        ({"input": 123}, "input"),
        ({"verdict": "maybe"}, "verdict"),
        ({"tags": ["billing", "not-an-option"]}, "tags"),
        ({"tags": "billing"}, "tags"),
    ],
)
def test_invalid_field_type_or_option_422(client, as_role, dataset_id, overrides, expected_column):
    as_role("annotator")
    data = {**VALID_DATA, **overrides}
    resp = client.post(f"/v1/datasets/{dataset_id}/rows", json={"data": data})
    assert resp.status_code == 422, resp.text
    assert f"column '{expected_column}'" in resp.json()["detail"]


@pytest.mark.parametrize("missing_key", ["input", "verdict"])
def test_missing_required_field_422(client, as_role, dataset_id, missing_key):
    as_role("annotator")
    data = {k: v for k, v in VALID_DATA.items() if k != missing_key}
    resp = client.post(f"/v1/datasets/{dataset_id}/rows", json={"data": data})
    assert resp.status_code == 422, resp.text
    assert f"column '{missing_key}'" in resp.json()["detail"]
    assert "required" in resp.json()["detail"]


def test_empty_required_field_422(client, as_role, dataset_id):
    as_role("annotator")
    data = {**VALID_DATA, "input": "   "}
    resp = client.post(f"/v1/datasets/{dataset_id}/rows", json={"data": data})
    assert resp.status_code == 422, resp.text
    assert "column 'input'" in resp.json()["detail"]


def test_archived_column_accepted_readonly_and_not_required(client, as_role, dataset_id):
    as_role("editor")
    # Archive "verdict" by dropping it from the schema.
    remaining = [c for c in SCHEMA_COLUMNS if c["key"] != "verdict"]
    resp = client.put(f"/v1/datasets/{dataset_id}/schema", json={"columns": remaining})
    assert resp.status_code == 200, resp.text

    as_role("annotator")
    data = {k: v for k, v in VALID_DATA.items() if k != "verdict"}
    data["verdict"] = "not-in-options-anymore"  # arbitrary value, column now archived
    resp = client.post(f"/v1/datasets/{dataset_id}/rows", json={"data": data})
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"]["verdict"] == "not-in-options-anymore"


def _make_row(dataset_id: str, data: dict, status: RowStatus, assignee: uuid.UUID | None) -> str:
    """Insert a row directly (status/assignee have no write endpoint until GL-2-3)."""
    db = SessionLocal()
    try:
        row = DatasetRow(
            dataset_id=uuid.UUID(dataset_id), data=data, status=status, assignee=assignee
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return str(row.id)
    finally:
        db.close()


@pytest.fixture()
def filter_rows(dataset_id):
    """Three rows spanning status/assignee/text combinations."""
    db = SessionLocal()
    try:
        alice = User(email=f"gl21-alice-{uuid.uuid4().hex[:8]}@example.com", display_name="Alice")
        bob = User(email=f"gl21-bob-{uuid.uuid4().hex[:8]}@example.com", display_name="Bob")
        db.add_all([alice, bob])
        db.commit()
        db.refresh(alice)
        db.refresh(bob)
    finally:
        db.close()

    row_a = _make_row(
        dataset_id,
        {**VALID_DATA, "input": "billing dispute about a late fee"},
        RowStatus.DRAFT,
        alice.id,
    )
    row_b = _make_row(
        dataset_id,
        {**VALID_DATA, "input": "eligibility question about coverage"},
        RowStatus.NEEDS_REVIEW,
        alice.id,
    )
    row_c = _make_row(
        dataset_id,
        {**VALID_DATA, "input": "billing dispute about a refund"},
        RowStatus.NEEDS_REVIEW,
        bob.id,
    )

    yield {"a": row_a, "b": row_b, "c": row_c, "alice": str(alice.id), "bob": str(bob.id)}

    db = SessionLocal()
    try:
        for user in db.query(User).filter(User.id.in_([alice.id, bob.id])):
            db.delete(user)
        db.commit()
    finally:
        db.close()


def test_filter_by_status(client, as_role, dataset_id, filter_rows):
    as_role("viewer")
    resp = client.get(f"/v1/datasets/{dataset_id}/rows", params={"status": "needs_review"})
    assert resp.status_code == 200, resp.text
    ids = {r["id"] for r in resp.json()}
    assert ids == {filter_rows["b"], filter_rows["c"]}


def test_filter_by_assignee(client, as_role, dataset_id, filter_rows):
    as_role("viewer")
    resp = client.get(f"/v1/datasets/{dataset_id}/rows", params={"assignee": filter_rows["bob"]})
    assert resp.status_code == 200, resp.text
    ids = {r["id"] for r in resp.json()}
    assert ids == {filter_rows["c"]}


def test_filter_by_q_over_text_columns(client, as_role, dataset_id, filter_rows):
    as_role("viewer")
    resp = client.get(f"/v1/datasets/{dataset_id}/rows", params={"q": "billing"})
    assert resp.status_code == 200, resp.text
    ids = {r["id"] for r in resp.json()}
    assert ids == {filter_rows["a"], filter_rows["c"]}


def test_filters_combine_with_and(client, as_role, dataset_id, filter_rows):
    as_role("viewer")
    resp = client.get(
        f"/v1/datasets/{dataset_id}/rows",
        params={"status": "needs_review", "assignee": filter_rows["alice"], "q": "eligibility"},
    )
    assert resp.status_code == 200, resp.text
    ids = {r["id"] for r in resp.json()}
    assert ids == {filter_rows["b"]}

    # Same status+assignee but a q that matches nothing for that assignee.
    resp = client.get(
        f"/v1/datasets/{dataset_id}/rows",
        params={"status": "needs_review", "assignee": filter_rows["alice"], "q": "refund"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == []
