"""GL-3.5-18: `rows delete` and `datasets delete`."""

from __future__ import annotations

import json

import httpx
import pytest
from groundline_cli.main import app
from groundline_cli.rowfile import save_state, state_path
from typer.testing import CliRunner

runner = CliRunner()
SCHEMA = {"columns": [{"key": "id", "label": "Id", "type": "text", "is_key": True, "required": True}]}


class FakeApi:
    def __init__(self, not_found=None, delete_status=204):
        self.requests: list[httpx.Request] = []
        self.not_found = not_found or []
        self.delete_status = delete_status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, method = request.url.path, request.method
        if path == "/v1/datasets":
            return httpx.Response(200, json=[{"id": "ds1", "name": "demo", "feature_id": None}])
        if path == "/v1/datasets/ds1/schema":
            return httpx.Response(200, json=SCHEMA)
        if path == "/v1/datasets/ds1/rows/delete":
            keys = json.loads(request.content)["keys"]
            if self.not_found:
                return httpx.Response(200, json={"deleted": 0, "not_found": self.not_found})
            return httpx.Response(200, json={"deleted": len(keys), "not_found": []})
        if path == "/v1/datasets/ds1" and method == "DELETE":
            if self.delete_status == 409:
                return httpx.Response(409, json={"detail": "dataset has 2 version(s) and cannot be deleted"})
            return httpx.Response(204)
        return httpx.Response(404, json={"detail": "not found"})

    def sent_keys(self) -> list[str]:
        return [json.loads(r.content)["keys"] for r in self.requests if r.url.path.endswith("/rows/delete")]


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUNDLINE_API_URL", "http://api.test")
    monkeypatch.setenv("GROUNDLINE_TOKEN", "tok")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_rows_delete_by_keys(env, install_transport):
    api = FakeApi()
    install_transport(api)
    result = runner.invoke(app, ["rows", "delete", "demo", "a", "b", "--yes"])
    assert result.exit_code == 0, result.output
    assert "deleted 2 row(s) from demo" in result.output
    assert api.sent_keys() == [["a", "b"]]


def test_rows_delete_by_file_asks_and_forgets_the_keys(env, install_transport):
    api = FakeApi()
    install_transport(api)
    wrong = env / "wrong.yaml"
    wrong.write_text("cases:\n  - {id: a, q: x}\n  - {id: b}\n")
    save_state(wrong, "demo", "ds1", "id", {"a": 1, "b": 2, "keep": 3}, {"a": "h", "b": "h", "keep": "h"})

    result = runner.invoke(app, ["rows", "delete", "demo", "--file", "wrong.yaml"], input="n\n")
    assert result.exit_code == 1
    assert api.sent_keys() == []

    result = runner.invoke(app, ["rows", "delete", "demo", "--file", "wrong.yaml"], input="y\n")
    assert result.exit_code == 0, result.output
    assert api.sent_keys() == [["a", "b"]]
    state = json.loads(state_path(wrong, "demo").read_text())
    assert state["revs"] == {"keep": 3} and set(state["bases"]) == {"keep"}


def test_rows_delete_reports_unknown_keys(env, install_transport):
    install_transport(FakeApi(not_found=["nope"]))
    result = runner.invoke(app, ["rows", "delete", "demo", "a", "nope", "--yes"])
    assert result.exit_code == 1
    assert "nothing deleted: not found in demo: nope" in result.output


def test_rows_delete_needs_keys_or_a_file(env, install_transport):
    install_transport(FakeApi())
    assert runner.invoke(app, ["rows", "delete", "demo"]).exit_code == 1
    (env / "f.yaml").write_text("- {id: a}\n")
    result = runner.invoke(app, ["rows", "delete", "demo", "a", "--file", "f.yaml"])
    assert result.exit_code == 1 and "not both" in result.output


def test_datasets_delete_confirms_the_name(env, install_transport):
    api = FakeApi()
    install_transport(api)
    result = runner.invoke(app, ["datasets", "delete", "demo"], input="dmeo\n")
    assert result.exit_code == 1 and "nothing deleted" in result.output
    assert not [r for r in api.requests if r.method == "DELETE"]

    result = runner.invoke(app, ["datasets", "delete", "demo"], input="demo\n")
    assert result.exit_code == 0, result.output
    assert "deleted demo" in result.output


def test_datasets_delete_with_versions_is_refused(env, install_transport):
    install_transport(FakeApi(delete_status=409))
    result = runner.invoke(app, ["datasets", "delete", "demo", "--yes"])
    assert result.exit_code == 1
    assert "cannot be deleted" in result.output


def test_rows_push_explains_deleted_keys(env, install_transport):
    result_body = {
        "applied": True, "created": 0, "updated": 0, "unchanged": 1, "server_newer": 0,
        "not_in_file": 0, "changes": [], "conflicts": [], "server_newer_keys": [],
        "deleted_keys": ["a"], "errors": [], "ignored_paths": [], "revs": {}, "bases": {},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/datasets/ds1/sync":
            return httpx.Response(200, json=result_body)
        return FakeApi()(request)

    install_transport(handler)
    (env / "f.yaml").write_text("- {id: a}\n")
    result = runner.invoke(app, ["rows", "push", "demo", "f.yaml"])
    assert result.exit_code == 0, result.output
    assert "1 row(s) were deleted in Groundline (a)" in result.output
    assert "--force to recreate" in result.output
