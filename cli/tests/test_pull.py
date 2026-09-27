from __future__ import annotations

import hashlib
import json

import httpx
import pytest
from groundline_cli.client import ApiClient, ApiError
from groundline_cli.pull import (
    HashMismatchError,
    check_drift,
    parse_target,
    pull_version,
)

JSONL_BYTES = b'{"id":"r1","status":"approved","data":{"a":"x"}}\n'
GOOD_HASH = "sha256:" + hashlib.sha256(JSONL_BYTES).hexdigest()

MANIFEST = {
    "dataset": "demo",
    "version": 1,
    "content_hash": GOOD_HASH,
    "row_count": 1,
    "created_at": "2026-09-27T00:00:00Z",
    "created_by": "dana@example.com",
    "notes": "",
}

SIDECAR = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "status": {"type": "string", "enum": ["draft", "needs_review", "approved"]},
        "data": {
            "type": "object",
            "properties": {"a": {"type": "string", "minLength": 1}},
            "additionalProperties": False,
            "required": ["a"],
        },
    },
    "required": ["id", "status", "data"],
    "additionalProperties": False,
}

CURRENT_SCHEMA = {
    "columns": [
        {
            "key": "a",
            "label": "A",
            "type": "text",
            "options": None,
            "required": True,
            "order": 0,
            "archived": False,
        }
    ]
}

DATASETS = [{"id": "ds1", "name": "demo", "feature_id": None}]


def _handler(manifest=MANIFEST, current_schema=CURRENT_SCHEMA):
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/datasets":
            return httpx.Response(200, json=DATASETS)
        if path == "/v1/datasets/ds1/versions/1":
            fmt = request.url.params.get("format")
            if fmt == "jsonl":
                return httpx.Response(200, content=JSONL_BYTES)
            return httpx.Response(200, json={"manifest": manifest, "rows": [
                {"id": "r1", "status": "approved", "data": {"a": "x"}}
            ]})
        if path == "/v1/datasets/ds1/versions/1/manifest":
            return httpx.Response(200, json=manifest)
        if path == "/v1/datasets/ds1/versions/1/jsonschema":
            return httpx.Response(200, json=SIDECAR)
        if path == "/v1/datasets/ds1/schema":
            return httpx.Response(200, json=current_schema)
        return httpx.Response(404, json={"detail": "not found"})

    return handle


def test_parse_target_ok():
    assert parse_target("demo@v3") == ("demo", 3)


def test_parse_target_bad_syntax():
    with pytest.raises(ApiError, match="invalid target"):
        parse_target("demo-v3")


def test_pull_version_writes_files_and_verifies_hash(tmp_path, config, install_transport):
    install_transport(_handler())
    with ApiClient(config) as client:
        content_hash = pull_version(client, "demo", 1, "jsonl", tmp_path)

    assert content_hash == GOOD_HASH
    version_dir = tmp_path / "demo" / "v1"
    assert (version_dir / "rows.jsonl").read_bytes() == JSONL_BYTES
    assert json.loads((version_dir / "manifest.json").read_text()) == MANIFEST
    assert json.loads((version_dir / "demo.schema.json").read_text()) == SIDECAR


def test_pull_version_json_format_fetches_wrapped_payload(tmp_path, config, install_transport):
    install_transport(_handler())
    with ApiClient(config) as client:
        pull_version(client, "demo", 1, "json", tmp_path)

    version_dir = tmp_path / "demo" / "v1"
    payload = json.loads((version_dir / "rows.json").read_text())
    assert payload["rows"] == [{"id": "r1", "status": "approved", "data": {"a": "x"}}]


def test_pull_version_hash_mismatch_leaves_no_data_file(tmp_path, config, install_transport):
    bad_manifest = dict(MANIFEST, content_hash="sha256:" + "0" * 64)
    install_transport(_handler(manifest=bad_manifest))

    with ApiClient(config) as client, pytest.raises(
        HashMismatchError, match="content hash mismatch"
    ):
        pull_version(client, "demo", 1, "jsonl", tmp_path)

    assert not (tmp_path / "demo").exists()


def test_check_drift_reports_missing_required_column(tmp_path, config, install_transport):
    drifted_schema = {
        "columns": CURRENT_SCHEMA["columns"]
        + [
            {
                "key": "b",
                "label": "B",
                "type": "text",
                "options": None,
                "required": True,
                "order": 1,
                "archived": False,
            }
        ]
    }
    install_transport(_handler(current_schema=drifted_schema))
    with ApiClient(config) as client:
        missing = check_drift(client, "demo", 1)

    assert missing == ["b"]


def test_check_drift_no_warning_when_schema_matches(config, install_transport):
    install_transport(_handler())
    with ApiClient(config) as client:
        missing = check_drift(client, "demo", 1)

    assert missing == []
