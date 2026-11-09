"""GL-142: count rows that fail only on their key, for the wrong-schema banner."""

from __future__ import annotations

import json
import uuid

import pytest
from groundline_api.db import SessionLocal
from groundline_api.models.dataset import Dataset

COLUMNS = [
    {"key": "id", "label": "ID", "type": "text", "required": True, "is_key": True},
    {"key": "score", "label": "Score", "type": "number"},
]
MAPPING = json.dumps({"id": "id", "score": "score"})


@pytest.fixture()
def dataset_id(client, as_role):
    as_role("editor")
    resp = client.post(
        "/v1/datasets",
        json={"name": f"gl142-{uuid.uuid4().hex[:8]}", "columns": COLUMNS},
    )
    assert resp.status_code == 201, resp.text
    ds_id = resp.json()["id"]
    resp = client.post(
        f"/v1/datasets/{ds_id}/import",
        files={"file": ("seed.csv", b"id,score\na,1\nb,2\nd,4\n")},
        data={"mapping": MAPPING},
    )
    assert resp.json()["imported"] == 3, resp.text
    yield ds_id
    db = SessionLocal()
    try:
        ds = db.get(Dataset, uuid.UUID(ds_id))
        if ds is not None:
            db.delete(ds)
            db.commit()
    finally:
        db.close()


def _validation(client, dataset_id, content):
    resp = client.post(
        f"/v1/datasets/{dataset_id}/import/preview",
        files={"file": ("data.csv", content)},
        data={"mapping": MAPPING},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["validation"]


def test_key_only_failures_are_counted(client, dataset_id):
    # a, b: already in the dataset. c: new, then repeated. d: existing key AND a bad number,
    # so a schema problem too. e: only a bad number.
    validation = _validation(
        client, dataset_id, b"id,score\na,1\nb,2\nc,3\nc,3\nd,x\ne,y\n"
    )
    assert validation["valid"] == 1
    assert validation["failed_rows"] == 5
    assert validation["existing_key_rows"] == 2
    assert validation["repeated_key_rows"] == 1


def test_no_key_failures(client, dataset_id):
    validation = _validation(client, dataset_id, b"id,score\nx,1\ny,z\n")
    assert validation["existing_key_rows"] == 0
    assert validation["repeated_key_rows"] == 0
    assert validation["failed_rows"] == 1
