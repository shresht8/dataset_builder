"""GL-1-6: dataset create/list/get with optional feature scope (§8, §11 Q3)."""

from __future__ import annotations

import uuid

from groundline_api.db import SessionLocal
from groundline_api.models.dataset import Dataset


def _cleanup(dataset_ids: list[str]) -> None:
    db = SessionLocal()
    try:
        for dataset_id in dataset_ids:
            dataset = db.get(Dataset, uuid.UUID(dataset_id))
            if dataset is not None:
                db.delete(dataset)
        db.commit()
    finally:
        db.close()


def test_create_get_with_and_without_feature_id(client, as_role):
    editor = as_role("editor")
    tag = uuid.uuid4().hex[:8]
    created = []
    try:
        # Unscoped dataset (default: no feature).
        resp = client.post("/v1/datasets", json={"name": f"gl16-plain-{tag}"})
        assert resp.status_code == 201, resp.text
        plain = resp.json()
        created.append(plain["id"])
        assert plain["feature_id"] is None
        assert plain["created_by"] == editor["id"]  # authenticated caller recorded

        # Feature-scoped dataset.
        resp = client.post(
            "/v1/datasets",
            json={"name": f"gl16-scoped-{tag}", "feature_id": f"feat-{tag}"},
        )
        assert resp.status_code == 201, resp.text
        scoped = resp.json()
        created.append(scoped["id"])
        assert scoped["feature_id"] == f"feat-{tag}"

        # GET single round-trips.
        resp = client.get(f"/v1/datasets/{scoped['id']}")
        assert resp.status_code == 200
        assert resp.json() == scoped

        # List filter by feature_id returns only the scoped dataset.
        resp = client.get("/v1/datasets", params={"feature_id": f"feat-{tag}"})
        assert resp.status_code == 200
        ids = [d["id"] for d in resp.json()]
        assert ids == [scoped["id"]]

        # Unfiltered list contains both.
        resp = client.get("/v1/datasets")
        assert resp.status_code == 200
        ids = [d["id"] for d in resp.json()]
        assert plain["id"] in ids and scoped["id"] in ids
    finally:
        _cleanup(created)


def test_get_dataset_404(client, as_role):
    as_role("viewer")
    resp = client.get(f"/v1/datasets/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "dataset not found"
