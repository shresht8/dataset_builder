from __future__ import annotations

import httpx
from groundline_cli.main import app
from typer.testing import CliRunner

runner = CliRunner()

DATASETS = [
    {"id": "ds1", "name": "demo", "feature_id": "feat_a", "created_by": None, "created_at": "x"},
]

VERSIONS = [{"version": 2, "id": "v2"}, {"version": 1, "id": "v1"}]

EMPTY_DIFF = {
    "from": 1,
    "to": 2,
    "added": [],
    "removed": [],
    "modified": [],
    "schema_changes": {"added": [], "removed": [], "changed": []},
}

FULL_DIFF = {
    "from": 1,
    "to": 2,
    "added": [{"id": "r2", "status": "approved", "data": {"a": "new"}}],
    "removed": [{"id": "r3", "status": "draft", "data": {"a": "gone"}}],
    "modified": [
        {
            "id": "r1",
            "status": {"old": "draft", "new": "approved"},
            "changes": [{"field": "a", "old": "x", "new": "y"}],
        }
    ],
    "schema_changes": {"added": ["b"], "removed": [], "changed": []},
}


def _base_env(monkeypatch):
    monkeypatch.setenv("GROUNDLINE_API_URL", "http://api.test")
    monkeypatch.setenv("GROUNDLINE_TOKEN", "tok")


def test_datasets_list_renders_table(monkeypatch, install_transport):
    _base_env(monkeypatch)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/datasets":
            return httpx.Response(200, json=DATASETS)
        if request.url.path == "/v1/datasets/ds1/versions":
            return httpx.Response(200, json=VERSIONS)
        return httpx.Response(404, json={"detail": "not found"})

    install_transport(handle)
    result = runner.invoke(app, ["datasets", "list"])

    assert result.exit_code == 0, result.output
    assert "demo" in result.output
    assert "ds1" in result.output
    assert "feat_a" in result.output
    assert "2" in result.output  # latest version


def test_datasets_diff_no_differences(monkeypatch, install_transport):
    _base_env(monkeypatch)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/datasets":
            return httpx.Response(200, json=DATASETS)
        if request.url.path == "/v1/datasets/ds1/versions/diff":
            return httpx.Response(200, json=EMPTY_DIFF)
        return httpx.Response(404, json={"detail": "not found"})

    install_transport(handle)
    result = runner.invoke(app, ["datasets", "diff", "demo", "v1", "v2"])

    assert result.exit_code == 0, result.output
    assert "no differences" in result.output


def test_datasets_diff_renders_changes(monkeypatch, install_transport):
    _base_env(monkeypatch)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/datasets":
            return httpx.Response(200, json=DATASETS)
        if request.url.path == "/v1/datasets/ds1/versions/diff":
            return httpx.Response(200, json=FULL_DIFF)
        return httpx.Response(404, json={"detail": "not found"})

    install_transport(handle)
    result = runner.invoke(app, ["datasets", "diff", "demo", "v1", "v2"])

    assert result.exit_code == 0, result.output
    assert "added (1)" in result.output
    assert "removed (1)" in result.output
    assert "modified (1)" in result.output
    assert "draft -> approved" in result.output
    assert "a: 'x' -> 'y'" in result.output
    assert "schema changes" in result.output
    assert "added: b" in result.output


def test_datasets_diff_bad_version_syntax(monkeypatch, install_transport):
    _base_env(monkeypatch)
    install_transport(lambda r: httpx.Response(200, json=DATASETS))

    result = runner.invoke(app, ["datasets", "diff", "demo", "1", "v2"])

    assert result.exit_code != 0
    assert "invalid version" in result.output
    assert "Traceback" not in result.output
