"""GL-3-5: export jsonl/json/yaml + JSON Schema sidecar (§7, C3).

Storage tests use their own per-run bucket (Rules for every agent).
"""

from __future__ import annotations

import hashlib
import json
import uuid

import boto3
import pytest
import yaml
from groundline_api.config import settings
from groundline_api.db import SessionLocal
from groundline_api.models.dataset import Dataset
from jsonschema import Draft202012Validator

COLUMNS = [
    {"key": "input", "label": "Input", "type": "long_text", "required": True},
    {
        "key": "verdict",
        "label": "Verdict",
        "type": "select",
        "options": ["correct", "incorrect"],
        "required": True,
    },
    {"key": "tags", "label": "Tags", "type": "multi_select", "options": ["a", "b"]},
    {"key": "score", "label": "Score", "type": "number"},
    {"key": "flagged", "label": "Flagged", "type": "boolean"},
]


@pytest.fixture(scope="module")
def storage_bucket():
    bucket = f"gl3-5-test-{uuid.uuid4().hex[:8]}"
    client = boto3.client(
        "s3",
        endpoint_url=settings.storage_endpoint_url,
        aws_access_key_id=settings.storage_access_key,
        aws_secret_access_key=settings.storage_secret_key,
    )
    client.create_bucket(Bucket=bucket)
    original_bucket = settings.storage_bucket
    settings.storage_bucket = bucket
    yield client, bucket
    settings.storage_bucket = original_bucket
    objects = client.list_objects_v2(Bucket=bucket).get("Contents", [])
    for obj in objects:
        client.delete_object(Bucket=bucket, Key=obj["Key"])
    client.delete_bucket(Bucket=bucket)


@pytest.fixture()
def dataset_id(client, as_role, storage_bucket):
    as_role("editor")
    resp = client.post("/v1/datasets", json={"name": f"gl35-{uuid.uuid4().hex[:8]}"})
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


def _create_row(client, dataset_id, data, approve=True):
    resp = client.post(f"/v1/datasets/{dataset_id}/rows", json={"data": data})
    assert resp.status_code == 201, resp.text
    row = resp.json()
    if approve:
        resp = client.patch(
            f"/v1/datasets/{dataset_id}/rows/{row['id']}",
            json={"status": "approved"},
            headers={"If-Match": str(row["rev"])},
        )
        assert resp.status_code == 200, resp.text
        row = resp.json()
    return row


def _get_object(storage_bucket, dataset_name, version, filename):
    client, bucket = storage_bucket
    key = f"datasets/{dataset_name}/v{version}/{filename}"
    return client.get_object(Bucket=bucket, Key=key)["Body"].read()


@pytest.fixture()
def cut_version(client, as_role, dataset_id, storage_bucket):
    """Approve a few rows, including edge cases, and cut v1. Returns (name, v, rows_data)."""
    as_role("annotator")
    row1 = _create_row(
        client,
        dataset_id,
        {
            "input": "multi\nline\ntext with unicode café",
            "verdict": "correct",
            "tags": ["a", "b"],
            "score": 3.5,
            "flagged": True,
        },
    )
    row2 = _create_row(
        client,
        dataset_id,
        {"input": "second row", "verdict": "incorrect"},  # optional cols omitted
    )

    as_role("editor")
    dataset_name = client.get(f"/v1/datasets/{dataset_id}").json()["name"]
    resp = client.post(f"/v1/datasets/{dataset_id}/versions", json={"notes": "export test"})
    assert resp.status_code == 201, resp.text
    version = resp.json()["version"]
    return dataset_name, version, [row1, row2]


def test_jsonl_bytes_identical_to_stored_and_hash_matches(
    client, as_role, dataset_id, storage_bucket, cut_version
):
    dataset_name, version, _ = cut_version
    stored_bytes = _get_object(storage_bucket, dataset_name, version, "rows.jsonl")

    as_role("viewer")
    resp = client.get(f"/v1/datasets/{dataset_id}/versions/{version}")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    assert resp.content == stored_bytes

    manifest = json.loads(_get_object(storage_bucket, dataset_name, version, "manifest.json"))
    content_hash = manifest["content_hash"].removeprefix("sha256:")
    assert hashlib.sha256(resp.content).hexdigest() == content_hash


def test_json_and_yaml_carry_same_content_as_jsonl(client, as_role, dataset_id, cut_version):
    _dataset_name, version, _ = cut_version
    as_role("viewer")

    jsonl_resp = client.get(f"/v1/datasets/{dataset_id}/versions/{version}?format=jsonl")
    jsonl_rows = [json.loads(line) for line in jsonl_resp.text.splitlines() if line]

    json_resp = client.get(f"/v1/datasets/{dataset_id}/versions/{version}?format=json")
    assert json_resp.status_code == 200, json_resp.text
    assert json_resp.headers["content-type"].startswith("application/json")
    json_body = json_resp.json()
    assert json_body["rows"] == jsonl_rows
    assert json_body["manifest"]["version"] == version

    yaml_resp = client.get(f"/v1/datasets/{dataset_id}/versions/{version}?format=yaml")
    assert yaml_resp.status_code == 200, yaml_resp.text
    assert yaml_resp.headers["content-type"].startswith("application/yaml")
    yaml_body = yaml.safe_load(yaml_resp.text)
    assert yaml_body == json_body

    # block scalar for the multi-line text field
    assert "|" in yaml_resp.text or "\n  " in yaml_resp.text


def test_every_exported_row_validates_against_sidecar(client, as_role, dataset_id, cut_version):
    _dataset_name, version, _ = cut_version
    as_role("viewer")

    sidecar_resp = client.get(f"/v1/datasets/{dataset_id}/versions/{version}/jsonschema")
    assert sidecar_resp.status_code == 200, sidecar_resp.text
    assert sidecar_resp.headers["content-type"].startswith("application/schema+json")
    schema = sidecar_resp.json()
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    jsonl_resp = client.get(f"/v1/datasets/{dataset_id}/versions/{version}?format=jsonl")
    rows = [json.loads(line) for line in jsonl_resp.text.splitlines() if line]
    assert len(rows) == 2
    for row in rows:
        errors = list(validator.iter_errors(row))
        assert not errors, f"row {row} failed sidecar validation: {errors}"


def test_sidecar_rejects_bad_select_value(client, as_role, dataset_id, cut_version):
    _, version, _ = cut_version
    as_role("viewer")
    schema = client.get(f"/v1/datasets/{dataset_id}/versions/{version}/jsonschema").json()
    validator = Draft202012Validator(schema)
    bad_row = {"id": str(uuid.uuid4()), "status": "approved", "data": {"input": "x", "verdict": "maybe"}}
    assert not validator.is_valid(bad_row)


def test_editing_rows_after_cut_does_not_change_export(client, as_role, dataset_id, cut_version):
    _dataset_name, version, rows = cut_version
    as_role("viewer")
    before = client.get(f"/v1/datasets/{dataset_id}/versions/{version}?format=json").json()

    as_role("editor")
    row = rows[0]
    client.patch(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}",
        json={"data": {"input": "changed after cut", "verdict": "incorrect"}},
        headers={"If-Match": str(row["rev"])},
    )

    as_role("viewer")
    after = client.get(f"/v1/datasets/{dataset_id}/versions/{version}?format=json").json()
    assert after == before


def test_viewer_can_export(client, as_role, dataset_id, cut_version):
    _, version, _ = cut_version
    as_role("viewer")
    resp = client.get(f"/v1/datasets/{dataset_id}/versions/{version}")
    assert resp.status_code == 200, resp.text


def test_bad_format_422(client, as_role, dataset_id, cut_version):
    _, version, _ = cut_version
    as_role("viewer")
    resp = client.get(f"/v1/datasets/{dataset_id}/versions/{version}?format=xml")
    assert resp.status_code == 422, resp.text


def test_missing_version_404(client, as_role, dataset_id):
    as_role("viewer")
    resp = client.get(f"/v1/datasets/{dataset_id}/versions/999")
    assert resp.status_code == 404, resp.text
    resp = client.get(f"/v1/datasets/{dataset_id}/versions/999/jsonschema")
    assert resp.status_code == 404, resp.text


def test_missing_dataset_404(client, as_role):
    as_role("viewer")
    resp = client.get(f"/v1/datasets/{uuid.uuid4()}/versions/1")
    assert resp.status_code == 404, resp.text
    resp = client.get(f"/v1/datasets/{uuid.uuid4()}/versions/1/jsonschema")
    assert resp.status_code == 404, resp.text
