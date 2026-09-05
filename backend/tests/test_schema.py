"""GL-1-7: column schema GET/PUT with typed validation (§3, §8)."""

from __future__ import annotations

import uuid

import pytest
from groundline_api.db import SessionLocal
from groundline_api.models.dataset import Dataset


@pytest.fixture()
def dataset_id(client, as_role):
    as_role("editor")  # schema writes are editor-only (§5)
    resp = client.post("/v1/datasets", json={"name": f"gl17-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 201, resp.text
    ds_id = resp.json()["id"]
    yield ds_id
    db = SessionLocal()
    try:
        ds = db.get(Dataset, uuid.UUID(ds_id))
        if ds is not None:
            db.delete(ds)  # columns cascade via FK
            db.commit()
    finally:
        db.close()


VALID_COLUMNS = [
    {"key": "input", "label": "Customer question", "type": "long_text", "required": True},
    {
        "key": "verdict",
        "label": "Correct?",
        "type": "select",
        "options": ["correct", "partially_correct", "incorrect"],
        "required": True,
    },
    {"key": "notes", "label": "Notes", "type": "text"},
]


def test_put_then_get_round_trip_order_preserved(client, dataset_id):
    resp = client.put(f"/v1/datasets/{dataset_id}/schema", json={"columns": VALID_COLUMNS})
    assert resp.status_code == 200, resp.text

    resp = client.get(f"/v1/datasets/{dataset_id}/schema")
    assert resp.status_code == 200
    cols = resp.json()["columns"]
    assert [c["key"] for c in cols] == ["input", "verdict", "notes"]
    assert [c["order"] for c in cols] == [0, 1, 2]
    assert cols[1]["options"] == ["correct", "partially_correct", "incorrect"]
    assert cols[0]["required"] is True and cols[2]["required"] is False
    assert all(c["archived"] is False for c in cols)


@pytest.mark.parametrize(
    "column, message_fragment",
    [
        (
            {"key": "x", "label": "X", "type": "dropdown"},
            None,  # pydantic enum error; body shape differs
        ),
        (
            {"key": "x", "label": "X", "type": "select", "options": []},
            "requires non-empty options",
        ),
        (
            {"key": "x", "label": "X", "type": "select"},
            "requires non-empty options",
        ),
        (
            {"key": "x", "label": "X", "type": "text", "options": ["a"]},
            "only allowed for select/multi_select",
        ),
    ],
)
def test_invalid_schema_422(client, dataset_id, column, message_fragment):
    resp = client.put(f"/v1/datasets/{dataset_id}/schema", json={"columns": [column]})
    assert resp.status_code == 422, resp.text
    if message_fragment:
        assert message_fragment in resp.json()["detail"]


def test_duplicate_keys_422(client, dataset_id):
    col = {"key": "dup", "label": "A", "type": "text"}
    resp = client.put(f"/v1/datasets/{dataset_id}/schema", json={"columns": [col, col]})
    assert resp.status_code == 422
    assert "duplicate column key" in resp.json()["detail"]


def test_removed_column_is_archived_not_deleted(client, dataset_id):
    client.put(f"/v1/datasets/{dataset_id}/schema", json={"columns": VALID_COLUMNS})

    # Drop "notes" from the schema.
    resp = client.put(
        f"/v1/datasets/{dataset_id}/schema", json={"columns": VALID_COLUMNS[:2]}
    )
    assert resp.status_code == 200
    assert [c["key"] for c in resp.json()["columns"]] == ["input", "verdict"]

    # Still present as archived, not hard-deleted.
    resp = client.get(
        f"/v1/datasets/{dataset_id}/schema", params={"include_archived": True}
    )
    archived = {c["key"]: c["archived"] for c in resp.json()["columns"]}
    assert archived == {"input": False, "verdict": False, "notes": True}

    # Re-adding the same key un-archives it.
    resp = client.put(f"/v1/datasets/{dataset_id}/schema", json={"columns": VALID_COLUMNS})
    assert resp.status_code == 200
    cols = resp.json()["columns"]
    assert [c["key"] for c in cols] == ["input", "verdict", "notes"]
    assert all(c["archived"] is False for c in cols)


def test_schema_404_when_dataset_missing(client, as_role):
    as_role("editor")
    missing = uuid.uuid4()
    assert client.get(f"/v1/datasets/{missing}/schema").status_code == 404
    resp = client.put(f"/v1/datasets/{missing}/schema", json={"columns": []})
    assert resp.status_code == 404
