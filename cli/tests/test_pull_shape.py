"""GL-3.5-13: `pull --shape`, local rendering from verified jsonl, and lock
entries that pin format + shape."""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest
import yaml
from groundline_cli.client import ApiClient
from groundline_cli.lock import LockError, read_lock, write_lock
from groundline_cli.main import app
from groundline_cli.pull import HashMismatchError, pull_version
from typer.testing import CliRunner

runner = CliRunner()

JSONL_BYTES = (
    b'{"data":{"expected.answer":"x","expected.calls":[{"args":{},"name":"f"}],"id":"k1"},'
    b'"id":"r1","status":"approved"}\n'
)
GOOD_HASH = "sha256:" + hashlib.sha256(JSONL_BYTES).hexdigest()
NESTED_DATA = {"expected": {"answer": "x", "calls": [{"args": {}, "name": "f"}]}, "id": "k1"}
MANIFEST = {
    "dataset": "demo",
    "version": 1,
    "content_hash": GOOD_HASH,
    "row_count": 1,
    "created_at": "2026-10-10T00:00:00Z",
    "created_by": "dana@example.com",
    "notes": "",
}
SIDECAR_FLAT = {"title": "flat"}
SIDECAR_NESTED = {"title": "nested"}
DATASETS = [{"id": "ds1", "name": "demo", "feature_id": None}]
CURRENT_SCHEMA = {"columns": []}


def _handler(requests: list, manifest=MANIFEST):
    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path == "/v1/datasets":
            return httpx.Response(200, json=DATASETS)
        if path == "/v1/datasets/ds1/versions/1":
            if request.url.params.get("format") == "jsonl":
                return httpx.Response(200, content=JSONL_BYTES)
            # The CLI must never use server-rendered bytes: they aren't verified.
            return httpx.Response(200, content=b"SERVER-RENDERED")
        if path == "/v1/datasets/ds1/versions/1/manifest":
            return httpx.Response(200, json=manifest)
        if path == "/v1/datasets/ds1/versions/1/jsonschema":
            nested = request.url.params.get("shape") == "nested"
            return httpx.Response(200, json=SIDECAR_NESTED if nested else SIDECAR_FLAT)
        if path == "/v1/datasets/ds1/schema":
            return httpx.Response(200, json=CURRENT_SCHEMA)
        return httpx.Response(404, json={"detail": "not found"})

    return handle


def _env(monkeypatch):
    monkeypatch.setenv("GROUNDLINE_API_URL", "http://api.test")
    monkeypatch.setenv("GROUNDLINE_TOKEN", "tok")


def _rendered_formats(requests) -> set[str]:
    return {
        r.url.params.get("format")
        for r in requests
        if r.url.path == "/v1/datasets/ds1/versions/1"
    }


def test_pull_nested_jsonl_renders_locally_with_nested_sidecar(tmp_path, config, install_transport):
    requests: list = []
    install_transport(_handler(requests))
    with ApiClient(config) as client:
        assert pull_version(client, "demo", 1, "jsonl", tmp_path, "nested") == GOOD_HASH

    version_dir = tmp_path / "demo" / "v1"
    lines = [json.loads(line) for line in (version_dir / "rows.jsonl").read_text().splitlines()]
    assert lines == [{"id": "r1", "status": "approved", "data": NESTED_DATA}]
    assert json.loads((version_dir / "demo.schema.json").read_text()) == SIDECAR_NESTED
    assert _rendered_formats(requests) == {"jsonl"}


def test_pull_yaml_nested_is_rendered_from_verified_bytes(tmp_path, config, install_transport):
    requests: list = []
    install_transport(_handler(requests))
    with ApiClient(config) as client:
        pull_version(client, "demo", 1, "yaml", tmp_path, "nested")

    payload = yaml.safe_load((tmp_path / "demo" / "v1" / "rows.yaml").read_text())
    assert payload["manifest"] == MANIFEST
    assert payload["rows"][0]["data"] == NESTED_DATA
    assert _rendered_formats(requests) == {"jsonl"}


def test_tampered_bytes_fail_before_anything_is_written(tmp_path, config, install_transport):
    bad = dict(MANIFEST, content_hash="sha256:" + "0" * 64)
    install_transport(_handler([], manifest=bad))
    with ApiClient(config) as client, pytest.raises(HashMismatchError):
        pull_version(client, "demo", 1, "yaml", tmp_path, "nested")
    assert not (tmp_path / "demo").exists()


def test_cli_pull_pins_format_and_shape(monkeypatch, tmp_path, install_transport):
    _env(monkeypatch)
    install_transport(_handler([]))
    lock_path = tmp_path / "groundline.lock"
    result = runner.invoke(
        app,
        ["pull", "demo@v1", "--format", "yaml", "--shape", "nested",
         "-o", str(tmp_path), "--lock-file", str(lock_path)],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(lock_path.read_text())["datasets"]["demo"] == {
        "version": 1, "content_hash": GOOD_HASH, "format": "yaml", "shape": "nested",
    }


def test_cli_pull_lock_restores_pinned_format_and_shape(monkeypatch, tmp_path, install_transport):
    _env(monkeypatch)
    install_transport(_handler([]))
    lock_path = tmp_path / "groundline.lock"
    write_lock(lock_path, {"demo": {
        "version": 1, "content_hash": GOOD_HASH, "format": "yaml", "shape": "nested",
    }})
    out_dir = tmp_path / "fresh"
    result = runner.invoke(app, ["pull", "--lock", "--lock-file", str(lock_path), "-o", str(out_dir)])
    assert result.exit_code == 0, result.output
    payload = yaml.safe_load((out_dir / "demo" / "v1" / "rows.yaml").read_text())
    assert payload["rows"][0]["data"] == NESTED_DATA
    assert json.loads((out_dir / "demo" / "v1" / "demo.schema.json").read_text()) == SIDECAR_NESTED


@pytest.mark.parametrize("flag", [["--shape", "flat"], ["--format", "jsonl"]])
def test_cli_pull_lock_conflicting_flag_exits_1_and_writes_nothing(
    monkeypatch, tmp_path, install_transport, flag
):
    _env(monkeypatch)
    install_transport(_handler([]))
    lock_path = tmp_path / "groundline.lock"
    write_lock(lock_path, {"demo": {
        "version": 1, "content_hash": GOOD_HASH, "format": "yaml", "shape": "nested",
    }})
    out_dir = tmp_path / "fresh"
    result = runner.invoke(
        app, ["pull", "--lock", *flag, "--lock-file", str(lock_path), "-o", str(out_dir)]
    )
    assert result.exit_code == 1
    assert "pinned as" in result.output
    assert not out_dir.exists()


def test_cli_pull_lock_old_entry_defaults_and_honours_flags(monkeypatch, tmp_path, install_transport):
    _env(monkeypatch)
    install_transport(_handler([]))
    lock_path = tmp_path / "groundline.lock"
    write_lock(lock_path, {"demo": {"version": 1, "content_hash": GOOD_HASH}})

    out_dir = tmp_path / "default"
    result = runner.invoke(app, ["pull", "--lock", "--lock-file", str(lock_path), "-o", str(out_dir)])
    assert result.exit_code == 0, result.output
    assert (out_dir / "demo" / "v1" / "rows.jsonl").read_bytes() == JSONL_BYTES

    out_dir = tmp_path / "flagged"
    result = runner.invoke(
        app,
        ["pull", "--lock", "--shape", "nested", "--lock-file", str(lock_path), "-o", str(out_dir)],
    )
    assert result.exit_code == 0, result.output
    line = json.loads((out_dir / "demo" / "v1" / "rows.jsonl").read_text())
    assert line["data"] == NESTED_DATA


def test_read_lock_rejects_unknown_shape(tmp_path):
    lock_path = tmp_path / "groundline.lock"
    write_lock(lock_path, {"demo": {"version": 1, "content_hash": GOOD_HASH, "shape": "deep"}})
    with pytest.raises(LockError, match="bad shape"):
        read_lock(lock_path)
