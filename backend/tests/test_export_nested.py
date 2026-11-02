"""GL-3.5-13: nested export shape + nested sidecar, and the shared renderer.

Uses the synthetic eval-suite fixture (Phase 3.5 decision k). Storage tests use
their own per-run bucket.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import boto3
import pytest
import yaml
from groundline_api.config import settings
from groundline_api.db import SessionLocal
from groundline_api.models.dataset import Dataset
from groundline_schema.paths import flatten
from groundline_schema.render import render
from jsonschema import Draft202012Validator

FIXTURE = Path(__file__).parent / "fixtures" / "eval_suite.yaml"
CALLS_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"name": {"type": "string"}, "args": {"type": "object"}},
        "required": ["name", "args"],
    },
}


def _cases() -> list[dict]:
    return yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))["cases"]


def _options(path: str) -> list[str]:
    return sorted({value for case in _cases() for value in flatten(case).get(path, [])})


def _columns() -> list[dict]:
    return [
        {"key": "id", "label": "ID", "type": "text", "required": True, "is_key": True},
        {"key": "prompt", "label": "Prompt", "type": "text", "required": True},
        {"key": "title", "label": "Title", "type": "text"},
        {"key": "topic", "label": "Topic", "type": "text"},
        {"key": "step", "label": "Step", "type": "number"},
        {"key": "thread_id", "label": "Thread", "type": "text"},
        {"key": "expected.routes", "label": "Routes", "type": "multi_select",
         "options": _options("expected.routes")},
        {"key": "expected.tools", "label": "Tools", "type": "multi_select",
         "options": _options("expected.tools")},
        {"key": "expected.calls", "label": "Calls", "type": "json", "json_schema": CALLS_SCHEMA},
        {"key": "expected.answer", "label": "Answer", "type": "long_text", "required": True},
        {"key": "expected.has_text", "label": "Has text", "type": "boolean"},
        {"key": "expected.has_table", "label": "Has table", "type": "boolean"},
        {"key": "expected.match_mode", "label": "Match mode", "type": "text"},
        {"key": "expected.follow_up", "label": "Follow-up", "type": "boolean"},
    ]


def _drop_nulls(value):
    """Null and absent are the same thing in Groundline (Phase 3.5 decision h)."""
    if isinstance(value, dict):
        return {k: _drop_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_drop_nulls(v) for v in value]
    return value


@pytest.fixture(scope="module")
def storage_bucket():
    bucket = f"gl3513-test-{uuid.uuid4().hex[:8]}"
    client = boto3.client(
        "s3",
        endpoint_url=settings.storage_endpoint_url,
        aws_access_key_id=settings.storage_access_key,
        aws_secret_access_key=settings.storage_secret_key,
    )
    client.create_bucket(Bucket=bucket)
    original_bucket = settings.storage_bucket
    settings.storage_bucket = bucket
    yield bucket
    settings.storage_bucket = original_bucket
    for obj in client.list_objects_v2(Bucket=bucket).get("Contents", []):
        client.delete_object(Bucket=bucket, Key=obj["Key"])
    client.delete_bucket(Bucket=bucket)


@pytest.fixture()
def version(client, as_role, storage_bucket):
    """A cut version of the fixture, stored flat; yields (dataset_id, version)."""
    as_role("editor")
    resp = client.post("/v1/datasets", json={"name": f"gl3513e-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 201, resp.text
    ds_id = resp.json()["id"]
    resp = client.put(f"/v1/datasets/{ds_id}/schema", json={"columns": _columns()})
    assert resp.status_code == 200, resp.text
    for case in _cases():
        data = flatten(case, leaves={"expected.calls"})
        resp = client.post(f"/v1/datasets/{ds_id}/rows", json={"data": data})
        assert resp.status_code == 201, resp.text
    resp = client.post(
        f"/v1/datasets/{ds_id}/versions", json={"notes": "fixture", "include_unapproved": True}
    )
    assert resp.status_code == 201, resp.text
    yield ds_id, resp.json()["version"]
    db = SessionLocal()
    try:
        ds = db.get(Dataset, uuid.UUID(ds_id))
        if ds is not None:
            db.delete(ds)
            db.commit()
    finally:
        db.close()


def _export(client, ds_id, v, fmt, shape):
    resp = client.get(f"/v1/datasets/{ds_id}/versions/{v}", params={"format": fmt, "shape": shape})
    assert resp.status_code == 200, resp.text
    return resp.content


def test_nested_export_equals_the_original_entries(client, version):
    ds_id, v = version
    lines = [json.loads(line) for line in _export(client, ds_id, v, "jsonl", "nested").splitlines()]
    by_id = {line["data"]["id"]: line["data"] for line in lines}
    assert {case["id"]: _drop_nulls(by_id[case["id"]]) for case in _cases()} == {
        case["id"]: _drop_nulls(case) for case in _cases()
    }
    # Aliases resolved to quoted strings, numbers and booleans kept typed.
    sales = by_id["case-sales-01"]["expected"]["calls"]
    assert sales[0]["args"]["to_date"] == "2099-03-31"
    assert sales[0]["args"]["limit"] == 5
    assert sales[1]["args"]["include_children"] is True
    assert by_id["case-orders-01"]["expected"]["calls"][0]["args"] == {}


def test_nested_rows_validate_against_nested_sidecar(client, version):
    ds_id, v = version
    resp = client.get(f"/v1/datasets/{ds_id}/versions/{v}/jsonschema", params={"shape": "nested"})
    assert resp.status_code == 200, resp.text
    sidecar = resp.json()
    data_schema = sidecar["properties"]["data"]
    assert data_schema["properties"]["expected"]["additionalProperties"] is False
    assert "expected" in data_schema["required"]  # expected.answer is required

    validator = Draft202012Validator(sidecar)
    for line in _export(client, ds_id, v, "jsonl", "nested").splitlines():
        errors = list(validator.iter_errors(json.loads(line)))
        assert not errors, errors

    flat_validator = Draft202012Validator(
        client.get(f"/v1/datasets/{ds_id}/versions/{v}/jsonschema").json()
    )
    for line in _export(client, ds_id, v, "jsonl", "flat").splitlines():
        assert flat_validator.is_valid(json.loads(line))


def test_nesting_is_a_rendering_flat_jsonl_matches_the_hash(client, version):
    ds_id, v = version
    flat = _export(client, ds_id, v, "jsonl", "flat")
    manifest = client.get(f"/v1/datasets/{ds_id}/versions/{v}/manifest").json()
    assert manifest["content_hash"] == "sha256:" + hashlib.sha256(flat).hexdigest()

    nested_jsonl = [json.loads(line) for line in _export(client, ds_id, v, "jsonl", "nested").splitlines()]
    nested_json = json.loads(_export(client, ds_id, v, "json", "nested"))
    nested_yaml = yaml.safe_load(_export(client, ds_id, v, "yaml", "nested"))
    assert nested_json["rows"] == nested_jsonl
    assert nested_yaml["rows"] == nested_jsonl
    assert nested_json["manifest"] == manifest


@pytest.mark.parametrize("fmt", ["jsonl", "json", "yaml"])
@pytest.mark.parametrize("shape", ["flat", "nested"])
def test_shared_renderer_is_byte_identical_to_the_api(client, version, fmt, shape):
    """What the CLI renders locally from verified jsonl is what the API serves."""
    ds_id, v = version
    rows_bytes = _export(client, ds_id, v, "jsonl", "flat")
    manifest = client.get(f"/v1/datasets/{ds_id}/versions/{v}/manifest").json()
    assert render(rows_bytes, manifest, fmt, shape) == _export(client, ds_id, v, fmt, shape)


def test_json_schema_survives_into_the_version_snapshot(client, version):
    ds_id, v = version
    sidecar = client.get(f"/v1/datasets/{ds_id}/versions/{v}/jsonschema").json()
    calls = sidecar["properties"]["data"]["properties"]["expected.calls"]
    assert calls == {"anyOf": [CALLS_SCHEMA, {"type": "null"}]}
