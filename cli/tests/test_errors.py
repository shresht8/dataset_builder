from __future__ import annotations

import httpx
from groundline_cli.main import app
from typer.testing import CliRunner

runner = CliRunner()

DATASETS = [{"id": "ds1", "name": "demo", "feature_id": None}]


def _base_env(monkeypatch):
    monkeypatch.setenv("GROUNDLINE_API_URL", "http://api.test")
    monkeypatch.setenv("GROUNDLINE_TOKEN", "tok")


def test_missing_config_gives_clean_error(monkeypatch, tmp_path):
    monkeypatch.delenv("GROUNDLINE_API_URL", raising=False)
    monkeypatch.delenv("GROUNDLINE_TOKEN", raising=False)
    monkeypatch.setattr("groundline_cli.config.CONFIG_PATH", tmp_path / "missing.toml")

    result = runner.invoke(app, ["datasets", "list"])

    assert result.exit_code != 0
    assert "missing configuration" in result.output
    assert "Traceback" not in result.output


def test_bad_token_gives_clean_401_error(monkeypatch, install_transport):
    _base_env(monkeypatch)
    install_transport(lambda r: httpx.Response(401, json={"detail": "invalid token"}))

    result = runner.invoke(app, ["datasets", "list"])

    assert result.exit_code != 0
    assert "authentication failed" in result.output
    assert "Traceback" not in result.output


def test_unknown_version_gives_clean_404_error(monkeypatch, install_transport):
    _base_env(monkeypatch)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/datasets":
            return httpx.Response(200, json=DATASETS)
        return httpx.Response(404, json={"detail": "version not found"})

    install_transport(handle)
    result = runner.invoke(app, ["pull", "demo@v99"])

    assert result.exit_code != 0
    assert "not found" in result.output
    assert "Traceback" not in result.output


def test_connection_refused_gives_clean_error(monkeypatch, install_transport):
    _base_env(monkeypatch)

    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    install_transport(handle)
    result = runner.invoke(app, ["datasets", "list"])

    assert result.exit_code != 0
    assert "could not connect" in result.output
    assert "Traceback" not in result.output


def test_bad_pull_target_syntax_gives_clean_error(monkeypatch):
    _base_env(monkeypatch)

    result = runner.invoke(app, ["pull", "not-a-valid-target"])

    assert result.exit_code != 0
    assert "invalid target" in result.output
    assert "Traceback" not in result.output


def test_pull_lock_not_implemented(monkeypatch):
    _base_env(monkeypatch)

    result = runner.invoke(app, ["pull", "--lock"])

    assert result.exit_code != 0
    assert "not yet implemented" in result.output
