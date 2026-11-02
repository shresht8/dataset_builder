"""GL-3.5-8: `date` values — row validation, sidecar, YAML export, diff."""

from __future__ import annotations

import json
import uuid

import boto3
import pytest
import yaml
from groundline_api.config import settings
from groundline_api.db import SessionLocal
from groundline_api.models.dataset import Dataset
from groundline_schema import Column
from groundline_schema.jsonschema import build_json_schema
from jsonschema import Draft202012Validator

COLUMNS = [
    {"key": "id", "label": "ID", "type": "text", "required": True},
    {"key": "as_of", "label": "As of", "type": "date", "required": True},
    {"key": "due", "label": "Due", "type": "date"},
]


@pytest.fixture(scope="module")
def storage_bucket():
    bucket = f"gl358-test-{uuid.uuid4().hex[:8]}"
    s3 = boto3.client(
        "s3",
        endpoint_url=settings.storage_endpoint_url,
        aws_access_key_id=settings.storage_access_key,
        aws_secret_access_key=settings.storage_secret_key,
    )
    s3.create_bucket(Bucket=bucket)
    original = settings.storage_bucket
    settings.storage_bucket = bucket
    yield bucket
    settings.storage_bucket = original
    for obj in s3.list_objects_v2(Bucket=bucket).get("Contents", []):
        s3.delete_object(Bucket=bucket, Key=obj["Key"])
    s3.delete_bucket(Bucket=bucket)


@pytest.fixture()
def dataset_id(client, as_role):
    as_role("editor")
    resp = client.post("/v1/datasets", json={"name": f"gl358-{uuid.uuid4().hex[:8]}"})
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


def _create(client, ds_id, data):
    return client.post(f"/v1/datasets/{ds_id}/rows", json={"data": data})


def test_valid_dates_are_stored_as_given(client, dataset_id):
    resp = _create(client, dataset_id, {"id": "a", "as_of": "2024-02-29", "due": "2099-12-31"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"]["as_of"] == "2024-02-29"

    row = resp.json()
    resp = client.patch(
        f"/v1/datasets/{dataset_id}/rows/{row['id']}",
        json={"data": {"due": "2100-01-01"}},
        headers={"If-Match": str(row["rev"])},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize(
    "value",
    ["2023-02-29", "2024-02-30", "0000-01-01", "10000-01-01", "2024-01-02T00:00:00Z", "02/01/2024", 20240102],
)
def test_invalid_dates_are_refused(client, dataset_id, value):
    resp = _create(client, dataset_id, {"id": "b", "as_of": value})
    assert resp.status_code == 422
    assert resp.json()["detail"].startswith("column 'as_of': expected a date as YYYY-MM-DD")


def test_optional_date_may_be_empty(client, dataset_id):
    assert _create(client, dataset_id, {"id": "c", "as_of": "2024-01-01", "due": None}).status_code == 201
    assert _create(client, dataset_id, {"id": "d", "as_of": "2024-01-01", "due": ""}).status_code == 201


def test_sidecar_pattern_does_the_work_without_format_checking():
    columns = [Column(**c) for c in COLUMNS]
    schema = build_json_schema(columns)
    assert schema["properties"]["as_of"] == {
        "type": "string", "format": "date", "pattern": r"^\d{4}-\d{2}-\d{2}$",
    }
    validator = Draft202012Validator(schema)  # no FormatChecker: `format` is annotation only
    assert validator.is_valid({"id": "a", "as_of": "2024-02-29"})
    assert not validator.is_valid({"id": "a", "as_of": "2024/02/29"})
    assert not validator.is_valid({"id": "a", "as_of": "2024-02-29T00:00:00Z"})


def test_versions_exports_and_diff_handle_dates(client, dataset_id, storage_bucket):
    _create(client, dataset_id, {"id": "a", "as_of": "2024-02-29", "due": "2099-12-31"})
    v1 = client.post(f"/v1/datasets/{dataset_id}/versions", json={"include_unapproved": True}).json()
    sidecar = client.get(f"/v1/datasets/{dataset_id}/versions/{v1['version']}/jsonschema").json()
    validator = Draft202012Validator(sidecar)

    jsonl = client.get(f"/v1/datasets/{dataset_id}/versions/{v1['version']}?format=jsonl").content
    yaml_text = client.get(f"/v1/datasets/{dataset_id}/versions/{v1['version']}?format=yaml").text
    as_json = client.get(f"/v1/datasets/{dataset_id}/versions/{v1['version']}?format=json").json()

    lines = [json.loads(line) for line in jsonl.splitlines()]
    assert all(validator.is_valid(line) for line in lines)
    assert all(validator.is_valid(row) for row in as_json["rows"])
    # YAML must quote the dates, or readers turn them into native date objects.
    assert "as_of: '2024-02-29'" in yaml_text
    from_yaml = yaml.safe_load(yaml_text)["rows"]
    assert from_yaml[0]["data"]["as_of"] == "2024-02-29"
    assert all(validator.is_valid(row) for row in from_yaml)

    rows = client.get(f"/v1/datasets/{dataset_id}/rows").json()
    client.patch(
        f"/v1/datasets/{dataset_id}/rows/{rows[0]['id']}",
        json={"data": {"due": "2100-01-01"}},
        headers={"If-Match": str(rows[0]["rev"])},
    )
    v2 = client.post(f"/v1/datasets/{dataset_id}/versions", json={"include_unapproved": True}).json()
    diff = client.get(
        f"/v1/datasets/{dataset_id}/versions/diff", params={"from": v1["version"], "to": v2["version"]}
    ).json()
    changes = diff["modified"][0]["changes"]
    assert changes == [{"field": "due", "old": "2099-12-31", "new": "2100-01-01"}]
