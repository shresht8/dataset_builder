"""GL-1-6: dataset create/list/get with optional feature scope (§8, §11 Q3)."""

from __future__ import annotations

import uuid

import pytest
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


def test_duplicate_name_conflict(client, as_role):
    """GL-3-15: a duplicate name returns 409, not 500."""
    as_role("editor")
    tag = uuid.uuid4().hex[:8]
    name = f"gl315-dup-{tag}"
    created = []
    try:
        resp = client.post("/v1/datasets", json={"name": name})
        assert resp.status_code == 201, resp.text
        created.append(resp.json()["id"])

        resp = client.post("/v1/datasets", json={"name": name})
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"] == f"dataset name '{name}' already exists"
    finally:
        _cleanup(created)


@pytest.mark.parametrize("name", ["Has Space", "a/b", "x@v1", "a.b", ""])
def test_invalid_name_rejected(client, as_role, name):
    """GL-3-15: names must match ^[a-z0-9][a-z0-9_-]{0,99}$."""
    as_role("editor")
    resp = client.post("/v1/datasets", json={"name": name})
    assert resp.status_code == 422, resp.text


def test_valid_name_created(client, as_role):
    """GL-3-15: a key-safe name is accepted."""
    as_role("editor")
    tag = uuid.uuid4().hex[:8]
    name = f"gl315-valid-{tag}"
    created = []
    try:
        resp = client.post("/v1/datasets", json={"name": name})
        assert resp.status_code == 201, resp.text
        created.append(resp.json()["id"])
        assert resp.json()["name"] == name
    finally:
        _cleanup(created)
