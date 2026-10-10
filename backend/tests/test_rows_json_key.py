"""GL-3.5-13: `json` row validation, the size cap, and key column rules."""

from __future__ import annotations

import uuid

import pytest
from groundline_api.config import settings
from groundline_api.db import SessionLocal
from groundline_api.models.dataset import Dataset
from groundline_schema import Column
from groundline_schema.jsonschema import build_json_schema
from jsonschema import Draft202012Validator

CALLS_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"name": {"type": "string"}, "args": {"type": "object"}},
        "required": ["name", "args"],
        "additionalProperties": False,
    },
}
COLUMNS = [
    {"key": "id", "label": "ID", "type": "text", "required": True, "is_key": True},
    {"key": "expected.calls", "label": "Calls", "type": "json", "json_schema": CALLS_SCHEMA},
    {"key": "meta", "label": "Meta", "type": "json", "required": True},
    {"key": "note", "label": "Note", "type": "text"},
]


@pytest.fixture()
def dataset_id(client, as_role):
    as_role("editor")
    resp = client.post("/v1/datasets", json={"name": f"gl3513r-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 201, resp.text
    ds_id = resp.json()["id"]
    resp = client.put(f"/v1/datasets/{ds_id}/schema", json={"columns": COLUMNS})
    assert resp.status_code == 200, resp.text
    yield ds_id
    db = SessionLocal()
    try:
        ds = db.get(Dataset, uuid.UUID(ds_id))
        if ds is not None:
            db.delete(ds)
            db.commit()
    finally:
        db.close()


def _create(client, dataset_id, data):
    return client.post(f"/v1/datasets/{dataset_id}/rows", json={"data": data})


def _patch(client, dataset_id, row, data):
    return client.patch(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}",
        json={"data": data},
        headers={"If-Match": str(row["rev"])},
    )


def test_json_value_checked_against_column_schema(client, dataset_id):
    good = [{"name": "get_forecast", "args": {"city": "Lisbon"}}, {"name": "f", "args": {}}]
    resp = _create(client, dataset_id, {"id": "a", "meta": {}, "expected.calls": good})
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"]["expected.calls"] == good

    bad = [{"name": "f", "args": "x"}]
    resp = _create(client, dataset_id, {"id": "b", "meta": {}, "expected.calls": bad})
    assert resp.status_code == 422
    assert resp.json()["detail"] == (
        "column 'expected.calls': [0].args: 'x' is not of type 'object'"
    )


@pytest.mark.parametrize("value", [[], {}, "", 0, False])
def test_required_json_accepts_non_null_empties(client, dataset_id, value):
    resp = _create(client, dataset_id, {"id": f"k{uuid.uuid4().hex[:6]}", "meta": value})
    assert resp.status_code == 201, resp.text


@pytest.mark.parametrize("data", [{"id": "n1", "meta": None}, {"id": "n2"}])
def test_required_json_rejects_null_or_missing(client, dataset_id, data):
    resp = _create(client, dataset_id, data)
    assert resp.status_code == 422
    assert resp.json()["detail"] == "column 'meta': required"


def test_sidecar_agrees_with_validation_for_json(client, dataset_id):
    columns = [Column(**c) for c in COLUMNS]
    validator = Draft202012Validator(build_json_schema(columns))
    cases = [
        ({"id": "s1", "meta": []}, True),
        ({"id": "s2", "meta": {"a": 1}, "expected.calls": None}, True),
        ({"id": "s3", "meta": None}, False),
        ({"id": "s4", "meta": 0, "expected.calls": [{"name": "f", "args": 1}]}, False),
    ]
    for data, valid in cases:
        accepted = _create(client, dataset_id, data).status_code == 201
        assert accepted is valid, data
        assert validator.is_valid(data) is valid, data


def test_json_cell_size_cap(client, dataset_id, monkeypatch):
    monkeypatch.setattr(settings, "json_cell_max_bytes", 50)
    resp = _create(client, dataset_id, {"id": "big", "meta": {"blob": "x" * 100}})
    assert resp.status_code == 422
    assert "the limit is 50" in resp.json()["detail"]


def test_duplicate_key_is_409_on_create_and_patch(client, dataset_id):
    first = _create(client, dataset_id, {"id": "a", "meta": {}}).json()
    resp = _create(client, dataset_id, {"id": "a", "meta": {}})
    assert resp.status_code == 409
    assert resp.json()["detail"] == f"key 'a' already exists (row {first['id']})"

    second = _create(client, dataset_id, {"id": "b", "meta": {}}).json()
    resp = _patch(client, dataset_id, second, {"id": "a"})
    assert resp.status_code == 409
    assert "key 'a' already exists" in resp.json()["detail"]


def test_key_value_rules(client, dataset_id):
    resp = _create(client, dataset_id, {"id": " a", "meta": {}})
    assert resp.status_code == 422
    assert "leading or trailing whitespace" in resp.json()["detail"]
    resp = _create(client, dataset_id, {"id": "", "meta": {}})
    assert resp.status_code == 422


def test_changing_key_needs_editor(client, dataset_id, as_role):
    row = _create(client, dataset_id, {"id": "a", "meta": {}}).json()

    as_role("annotator")
    resp = _patch(client, dataset_id, row, {"id": "renamed"})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "changing the key column requires editor role"
    # Other fields, and the unchanged key value, are fine for an annotator.
    resp = _patch(client, dataset_id, row, {"id": "a", "note": "ok"})
    assert resp.status_code == 200, resp.text
    row = resp.json()

    as_role("editor")
    resp = _patch(client, dataset_id, row, {"id": "renamed"})
    assert resp.status_code == 200, resp.text
    # The old key is free again.
    assert _create(client, dataset_id, {"id": "a", "meta": {}}).status_code == 201


def test_annotator_can_create_a_keyed_row(client, dataset_id, as_role):
    as_role("annotator")
    assert _create(client, dataset_id, {"id": "from-annotator", "meta": {}}).status_code == 201
