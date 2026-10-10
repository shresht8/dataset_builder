"""GL-3.5-8: the CLI with `date` columns — pull moves bytes untouched, infer drafts `date`."""

from __future__ import annotations

import hashlib
import json

import httpx
import yaml
from groundline_cli.client import ApiClient
from groundline_cli.main import app
from groundline_cli.pull import pull_version
from typer.testing import CliRunner

runner = CliRunner()

JSONL_BYTES = b'{"data":{"as_of":"2024-02-29","id":"a"},"id":"r1","status":"approved"}\n'
MANIFEST = {
    "dataset": "demo", "version": 1,
    "content_hash": "sha256:" + hashlib.sha256(JSONL_BYTES).hexdigest(),
    "row_count": 1, "created_at": "2026-10-10T00:00:00Z", "created_by": "d@example.com", "notes": "",
}


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/v1/datasets":
        return httpx.Response(200, json=[{"id": "ds1", "name": "demo", "feature_id": None}])
    if path == "/v1/datasets/ds1/versions/1":
        return httpx.Response(200, content=JSONL_BYTES)
    if path == "/v1/datasets/ds1/versions/1/manifest":
        return httpx.Response(200, json=MANIFEST)
    if path == "/v1/datasets/ds1/versions/1/jsonschema":
        return httpx.Response(200, json={})
    return httpx.Response(404, json={"detail": "not found"})


def test_pull_keeps_dates_as_strings_in_every_format(tmp_path, config, install_transport):
    install_transport(_handler)
    with ApiClient(config) as client:
        for fmt in ("jsonl", "json", "yaml"):
            pull_version(client, "demo", 1, fmt, tmp_path)

    version_dir = tmp_path / "demo" / "v1"
    assert (version_dir / "rows.jsonl").read_bytes() == JSONL_BYTES
    assert json.loads((version_dir / "rows.json").read_text())["rows"][0]["data"]["as_of"] == "2024-02-29"
    yaml_text = (version_dir / "rows.yaml").read_text()
    assert "as_of: '2024-02-29'" in yaml_text  # quoted, so YAML readers keep a string
    assert yaml.safe_load(yaml_text)["rows"][0]["data"]["as_of"] == "2024-02-29"


def test_schema_infer_drafts_date_columns(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data = tmp_path / "d.yaml"
    data.write_text(
        "- {id: a, as_of: '2024-02-29', note: '2024-02-29 is a leap day', when: 2024-03-01}\n"
        "- {id: b, as_of: '2025-01-01', note: plain, when: 2024-03-02}\n"
    )
    result = runner.invoke(app, ["schema", "infer", str(data)])
    assert result.exit_code == 0, result.output
    columns = {c["key"]: c["type"] for c in yaml.safe_load(result.output)["columns"]}
    assert columns == {"id": "text", "as_of": "date", "note": "text", "when": "date"}
