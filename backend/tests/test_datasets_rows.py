"""GL-1-4: dataset + row create/list against the real local Postgres.

Each test creates uniquely named data and deletes it afterwards so reruns pass.
"""

from __future__ import annotations

import uuid

from groundline_api.db import SessionLocal
from groundline_api.models.dataset import Dataset


def _cleanup(dataset_id: str) -> None:
    """Delete the dataset; rows cascade via FK ondelete."""
    db = SessionLocal()
    try:
        dataset = db.get(Dataset, uuid.UUID(dataset_id))
        if dataset is not None:
            db.delete(dataset)
            db.commit()
    finally:
        db.close()


def test_create_dataset_then_rows_round_trip(client, as_role):
    as_role("editor")  # editor may create datasets and rows (§5)
    name = f"gl14-test-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/v1/datasets", json={"name": name, "description": "GL-1-4 round trip"}
    )
    assert resp.status_code == 201, resp.text
    dataset = resp.json()
    dataset_id = dataset["id"]
    try:
        assert dataset["name"] == name
        assert dataset["description"] == "GL-1-4 round trip"
        assert dataset["created_at"]

        # Dataset shows up in the list.
        resp = client.get("/v1/datasets")
        assert resp.status_code == 200
        assert dataset_id in [d["id"] for d in resp.json()]

        # Create two rows, list them back in order.
        row_payloads = [{"input": "hi", "n": 1}, {"input": "bye", "n": 2}]
        for data in row_payloads:
            resp = client.post(f"/v1/datasets/{dataset_id}/rows", json={"data": data})
            assert resp.status_code == 201, resp.text
            body = resp.json()
            assert body["dataset_id"] == dataset_id
            assert body["data"] == data

        resp = client.get(f"/v1/datasets/{dataset_id}/rows")
        assert resp.status_code == 200
        rows = resp.json()
        assert [r["data"] for r in rows] == row_payloads
    finally:
        _cleanup(dataset_id)


def test_rows_404_when_dataset_missing(client, as_role):
    as_role("annotator")  # annotator may read and create rows (§5)
    missing = uuid.uuid4()
    resp = client.get(f"/v1/datasets/{missing}/rows")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "dataset not found"

    resp = client.post(f"/v1/datasets/{missing}/rows", json={"data": {"x": 1}})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "dataset not found"
