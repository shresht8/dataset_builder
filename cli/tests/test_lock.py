from __future__ import annotations

import hashlib
import json

import httpx
import pytest
from groundline_cli.client import ApiClient
from groundline_cli.lock import (
    LockError,
    read_lock,
    restore_lock,
    update_lock,
    write_lock,
)
from groundline_cli.main import app
from typer.testing import CliRunner

runner = CliRunner()

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

JSONL_BYTES_V2 = b'{"id":"r2","status":"approved","data":{"a":"y"}}\n'
GOOD_HASH_V2 = "sha256:" + hashlib.sha256(JSONL_BYTES_V2).hexdigest()
MANIFEST_V2 = dict(MANIFEST, version=2, content_hash=GOOD_HASH_V2)


def _handler(datasets=DATASETS, manifest=MANIFEST):
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/datasets":
            return httpx.Response(200, json=datasets)
        if path.endswith("/versions/1"):
            fmt = request.url.params.get("format")
            if fmt == "jsonl":
                return httpx.Response(200, content=JSONL_BYTES)
            return httpx.Response(200, json={"manifest": manifest, "rows": []})
        if path.endswith("/versions/2"):
            fmt = request.url.params.get("format")
            if fmt == "jsonl":
                return httpx.Response(200, content=JSONL_BYTES_V2)
            return httpx.Response(200, json={"manifest": MANIFEST_V2, "rows": []})
        if path.endswith("/versions/1/manifest"):
            return httpx.Response(200, json=manifest)
        if path.endswith("/versions/2/manifest"):
            return httpx.Response(200, json=MANIFEST_V2)
        if path.endswith("/jsonschema"):
            return httpx.Response(200, json=SIDECAR)
        if path.endswith("/schema"):
            return httpx.Response(200, json=CURRENT_SCHEMA)
        return httpx.Response(404, json={"detail": "not found"})

    return handle


def _base_env(monkeypatch):
    monkeypatch.setenv("GROUNDLINE_API_URL", "http://api.test")
    monkeypatch.setenv("GROUNDLINE_TOKEN", "tok")


def test_write_lock_is_deterministic_and_sorted(tmp_path):
    path = tmp_path / "groundline.lock"
    write_lock(
        path,
        {
            "zeta": {"version": 1, "content_hash": "sha256:" + "1" * 64},
            "alpha": {"version": 2, "content_hash": "sha256:" + "2" * 64},
        },
    )
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    data = json.loads(text)
    assert list(data["datasets"].keys()) == ["alpha", "zeta"]

    # Writing the same content again produces byte-identical output.
    write_lock(path, dict(data["datasets"]))
    assert path.read_text(encoding="utf-8") == text


def test_update_lock_creates_file_if_absent(tmp_path):
    path = tmp_path / "groundline.lock"
    assert not path.exists()
    update_lock(path, "demo", 1, "sha256:" + "a" * 64)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"datasets": {"demo": {"version": 1, "content_hash": "sha256:" + "a" * 64}}}


def test_update_lock_switching_version_rewrites_only_that_entry(tmp_path):
    path = tmp_path / "groundline.lock"
    update_lock(path, "demo", 1, "sha256:" + "a" * 64)
    update_lock(path, "other", 1, "sha256:" + "b" * 64)
    before = json.loads(path.read_text(encoding="utf-8"))

    update_lock(path, "demo", 2, "sha256:" + "c" * 64)
    after = json.loads(path.read_text(encoding="utf-8"))

    assert after["datasets"]["other"] == before["datasets"]["other"]
    assert after["datasets"]["demo"] == {"version": 2, "content_hash": "sha256:" + "c" * 64}


def test_read_lock_missing_file(tmp_path):
    with pytest.raises(LockError, match="not found"):
        read_lock(tmp_path / "groundline.lock")


def test_read_lock_malformed_json(tmp_path):
    path = tmp_path / "groundline.lock"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(LockError, match="malformed"):
        read_lock(path)


def test_read_lock_empty(tmp_path):
    path = tmp_path / "groundline.lock"
    path.write_text(json.dumps({"datasets": {}}), encoding="utf-8")
    with pytest.raises(LockError, match="no pinned datasets"):
        read_lock(path)


def test_read_lock_malformed_entry(tmp_path):
    path = tmp_path / "groundline.lock"
    path.write_text(json.dumps({"datasets": {"demo": {"version": 1}}}), encoding="utf-8")
    with pytest.raises(LockError, match="malformed lock entry"):
        read_lock(path)


def test_restore_lock_restores_all_pinned(tmp_path, config, install_transport):
    install_transport(_handler())
    lock_path = tmp_path / "groundline.lock"
    write_lock(lock_path, {"demo": {"version": 1, "content_hash": GOOD_HASH}})

    with ApiClient(config) as client:
        restored = restore_lock(client, lock_path, "jsonl", tmp_path)

    assert restored == [("demo", 1, GOOD_HASH)]
    assert (tmp_path / "demo" / "v1" / "rows.jsonl").read_bytes() == JSONL_BYTES


def test_restore_lock_tampered_hash_fails_and_leaves_no_data(tmp_path, config, install_transport):
    install_transport(_handler())
    lock_path = tmp_path / "groundline.lock"
    tampered = "sha256:" + "0" * 64
    write_lock(lock_path, {"demo": {"version": 1, "content_hash": tampered}})

    with ApiClient(config) as client, pytest.raises(LockError, match="lock verification failed"):
        restore_lock(client, lock_path, "jsonl", tmp_path)

    assert not (tmp_path / "demo").exists()


def test_cli_pull_updates_lock_entry(monkeypatch, tmp_path, install_transport):
    _base_env(monkeypatch)
    install_transport(_handler())
    lock_path = tmp_path / "groundline.lock"

    result = runner.invoke(
        app,
        ["pull", "demo@v1", "-o", str(tmp_path), "--lock-file", str(lock_path)],
    )

    assert result.exit_code == 0, result.output
    data = json.loads(lock_path.read_text(encoding="utf-8"))
    assert data == {"datasets": {"demo": {"version": 1, "content_hash": GOOD_HASH}}}


def test_cli_pull_switching_version_rewrites_only_that_entry_deterministically(
    monkeypatch, tmp_path, install_transport
):
    _base_env(monkeypatch)
    install_transport(_handler())
    lock_path = tmp_path / "groundline.lock"
    update_lock(lock_path, "other", 1, "sha256:" + "b" * 64)
    before = lock_path.read_text(encoding="utf-8")

    result = runner.invoke(
        app,
        ["pull", "demo@v1", "-o", str(tmp_path), "--lock-file", str(lock_path)],
    )
    assert result.exit_code == 0, result.output
    after_v1 = json.loads(lock_path.read_text(encoding="utf-8"))

    result = runner.invoke(
        app,
        ["pull", "demo@v2", "-o", str(tmp_path), "--lock-file", str(lock_path)],
    )
    assert result.exit_code == 0, result.output
    after_v2 = json.loads(lock_path.read_text(encoding="utf-8"))

    assert json.loads(before)["datasets"]["other"] == after_v2["datasets"]["other"]
    assert after_v1["datasets"]["demo"] == {"version": 1, "content_hash": GOOD_HASH}
    assert after_v2["datasets"]["demo"] == {"version": 2, "content_hash": GOOD_HASH_V2}


def test_cli_pull_lock_restores_all_pinned(monkeypatch, tmp_path, install_transport):
    _base_env(monkeypatch)
    install_transport(_handler())
    lock_path = tmp_path / "groundline.lock"
    write_lock(lock_path, {"demo": {"version": 1, "content_hash": GOOD_HASH}})
    out_dir = tmp_path / "fresh"

    result = runner.invoke(
        app,
        ["pull", "--lock", "--lock-file", str(lock_path), "-o", str(out_dir)],
    )

    assert result.exit_code == 0, result.output
    assert (out_dir / "demo" / "v1" / "rows.jsonl").read_bytes() == JSONL_BYTES
    assert f"({GOOD_HASH})" in result.output


def test_cli_pull_lock_tampered_hash_nonzero_exit_no_data(monkeypatch, tmp_path, install_transport):
    _base_env(monkeypatch)
    install_transport(_handler())
    lock_path = tmp_path / "groundline.lock"
    tampered = "sha256:" + "0" * 64
    write_lock(lock_path, {"demo": {"version": 1, "content_hash": tampered}})
    out_dir = tmp_path / "fresh"

    result = runner.invoke(
        app,
        ["pull", "--lock", "--lock-file", str(lock_path), "-o", str(out_dir)],
    )

    assert result.exit_code != 0
    assert "lock verification failed" in result.output
    assert tampered in result.output
    assert GOOD_HASH in result.output
    assert not (out_dir / "demo").exists()
