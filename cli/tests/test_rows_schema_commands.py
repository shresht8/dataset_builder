"""GL-3.5-16: `schema infer|pull|push`, `datasets create`, `rows pull|push`."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import yaml
from groundline_cli.main import app
from groundline_cli.rowfile import state_path
from groundline_cli.schemafile import NULLS_ONLY
from groundline_schema import Column
from groundline_schema.paths import row_hash
from typer.testing import CliRunner

runner = CliRunner()
FIXTURE = Path(__file__).parents[2] / "backend" / "tests" / "fixtures" / "eval_suite.yaml"

COLUMNS = [
    {"key": "id", "label": "Id", "type": "text", "options": None, "required": True,
     "order": 0, "archived": False, "json_schema": None, "is_key": True},
    {"key": "question", "label": "Question", "type": "text", "options": None,
     "required": False, "order": 1, "archived": False, "json_schema": None, "is_key": False},
    {"key": "expected.calls", "label": "Calls", "type": "json", "options": None,
     "required": False, "order": 2, "archived": False, "json_schema": None, "is_key": False},
]
ROWS = [
    {"id": "r1", "rev": 3, "status": "draft",
     "data": {"id": "a", "question": "qa", "expected.calls": [{"name": "f", "args": {"z": 1, "a": 2}}]}},
    {"id": "r2", "rev": 1, "status": "draft", "data": {"id": "b", "question": None}},
]
KEYS = {c["key"] for c in COLUMNS}


class FakeApi:
    """An in-memory stand-in for the endpoints these commands call."""

    def __init__(self, sync_result=None, schema_put_status=200, create_status=201):
        self.requests: list[httpx.Request] = []
        self.sync_result = sync_result or {}
        self.schema_put_status = schema_put_status
        self.create_status = create_status
        self.schema = [dict(c) for c in COLUMNS]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, method = request.url.path, request.method
        if path == "/v1/datasets" and method == "GET":
            return httpx.Response(200, json=[{"id": "ds1", "name": "demo", "feature_id": None}])
        if path == "/v1/datasets" and method == "POST":
            if self.create_status == 201:
                return httpx.Response(201, json={"id": "ds1", "name": "demo"})
            if self.create_status == 409:
                return httpx.Response(409, json={"detail": "dataset name 'demo' already exists"})
            return httpx.Response(422, json={"detail": [
                {"loc": ["body", "name"], "msg": "String should match pattern"}]})
        if path == "/v1/datasets/ds1/schema" and method == "GET":
            return httpx.Response(200, json={"columns": self.schema})
        if path == "/v1/datasets/ds1/schema" and method == "PUT":
            if self.schema_put_status != 200:
                return httpx.Response(self.schema_put_status, json={"detail": "duplicate column key: 'x'"})
            return httpx.Response(200, json=json.loads(request.content))
        if path == "/v1/datasets/ds1/rows":
            return httpx.Response(200, json=ROWS)
        if path == "/v1/datasets/ds1/sync":
            return httpx.Response(200, json=self.sync_result)
        return httpx.Response(404, json={"detail": "not found"})

    def calls(self, method: str, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and r.url.path == path]


def _result(**overrides) -> dict:
    result = {
        "applied": True, "created": 0, "updated": 0, "unchanged": 0, "server_newer": 0,
        "not_in_file": 0, "changes": [], "conflicts": [], "server_newer_keys": [],
        "errors": [], "ignored_paths": [], "revs": {}, "bases": {},
    }
    result.update(overrides)
    return result


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUNDLINE_API_URL", "http://api.test")
    monkeypatch.setenv("GROUNDLINE_TOKEN", "tok")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _form(request: httpx.Request) -> dict[str, str]:
    """The non-file fields of a multipart request."""
    body = request.content.decode("utf-8", errors="replace")
    fields = {}
    for part in body.split("--")[1:]:
        if 'name="' not in part or 'filename="' in part:
            continue
        name = part.split('name="', 1)[1].split('"', 1)[0]
        fields[name] = part.split("\r\n\r\n", 1)[1].rsplit("\r\n", 1)[0]
    return fields


# --- schema infer ---------------------------------------------------------------


def test_schema_infer_drafts_types_from_the_fixture(env):
    out = env / "suite.schema.yaml"
    result = runner.invoke(app, ["schema", "infer", str(FIXTURE), "-o", str(out)])
    assert result.exit_code == 0, result.output
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# Draft inferred from eval_suite.yaml (4 records)")
    assert f"# thread_id: {NULLS_ONLY}" in text

    columns = {c["key"]: c for c in yaml.safe_load(text)["columns"]}
    for column in columns.values():
        Column.model_validate(column)
    assert columns["id"] == {"key": "id", "label": "Id", "type": "text",
                             "required": True, "is_key": True}
    assert columns["expected.calls"]["type"] == "json"
    assert columns["expected.routes"]["type"] == "multi_select"
    assert columns["expected.tools"]["options"] == [
        "cost_report", "get_balance", "get_contacts", "get_forecast", "get_policy",
        "list_late_orders", "top_items",
    ]
    assert columns["expected.answer"]["type"] == "long_text"
    assert columns["step"]["type"] == "number"
    assert columns["thread_id"]["type"] == "text"
    assert columns["expected.has_text"]["type"] == "boolean"
    assert columns["expected.match_mode"]["type"] == "text"
    assert columns["expected.answer"]["label"] == "Answer"


def test_schema_infer_without_a_usable_key_warns(env):
    data = env / "d.json"
    data.write_text(json.dumps([{"name": "a"}, {"name": "a"}]))
    result = runner.invoke(app, ["schema", "infer", str(data)])
    assert result.exit_code == 0
    assert "warning: no key column found" in result.output

    result = runner.invoke(app, ["schema", "infer", str(data), "--key", "name"])
    assert result.exit_code == 1
    assert "must be a non-empty, unique text value" in result.output


# --- datasets create ------------------------------------------------------------


def _schema_file(env) -> Path:
    path = env / "s.yaml"
    path.write_text(yaml.safe_dump({"columns": [
        {"key": "id", "label": "Id", "type": "text", "required": True, "is_key": True},
    ]}))
    return path


def test_datasets_create_posts_then_puts_the_schema(env, install_transport):
    api = FakeApi()
    install_transport(api)
    result = runner.invoke(app, ["datasets", "create", "demo", "--schema", str(_schema_file(env))])
    assert result.exit_code == 0, result.output
    assert "created demo with 1 columns" in result.output
    put = api.calls("PUT", "/v1/datasets/ds1/schema")
    assert json.loads(put[0].content)["columns"][0]["is_key"] is True


@pytest.mark.parametrize("status, message", [
    (409, "dataset name 'demo' already exists"),
    (422, "name: String should match pattern"),
])
def test_datasets_create_surfaces_name_rules(env, install_transport, status, message):
    install_transport(FakeApi(create_status=status))
    result = runner.invoke(app, ["datasets", "create", "demo", "--schema", str(_schema_file(env))])
    assert result.exit_code == 1
    assert message in result.output


def test_datasets_create_reports_a_failed_schema(env, install_transport):
    install_transport(FakeApi(schema_put_status=422))
    schema = _schema_file(env)
    result = runner.invoke(app, ["datasets", "create", "demo", "--schema", str(schema)])
    assert result.exit_code == 1
    assert "was created without a schema" in result.output
    assert f"groundline schema push demo {schema}" in result.output


def test_datasets_create_validates_the_file_locally(env, install_transport):
    api = FakeApi()
    install_transport(api)
    bad = env / "bad.yaml"
    bad.write_text("columns:\n- key: x\n  label: X\n  type: nope\n")
    result = runner.invoke(app, ["datasets", "create", "demo", "--schema", str(bad)])
    assert result.exit_code == 1
    assert "column 1: type" in result.output
    assert api.requests == []


# --- schema pull / push ---------------------------------------------------------


def test_schema_pull_writes_a_schema_file(env, install_transport):
    install_transport(FakeApi())
    result = runner.invoke(app, ["schema", "pull", "demo", "-o", "s.yaml"])
    assert result.exit_code == 0, result.output
    columns = yaml.safe_load((env / "s.yaml").read_text())["columns"]
    assert columns[0] == {"key": "id", "label": "Id", "type": "text", "required": True, "is_key": True}
    assert "order" not in columns[1] and "archived" not in columns[1]


def test_schema_push_dry_run_prompt_and_yes(env, install_transport):
    api = FakeApi()
    install_transport(api)
    path = env / "fewer.yaml"
    path.write_text(yaml.safe_dump({"columns": [
        {"key": "id", "label": "Id", "type": "text", "required": True, "is_key": True},
        {"key": "question", "label": "Question!", "type": "text"},
        {"key": "new", "label": "New", "type": "number"},
    ]}))

    result = runner.invoke(app, ["schema", "push", "demo", str(path), "--dry-run"])
    assert result.exit_code == 0
    assert "added: new" in result.output
    assert "changed: question" in result.output
    assert "archived: expected.calls" in result.output

    result = runner.invoke(app, ["schema", "push", "demo", str(path)], input="n\n")
    assert result.exit_code == 1
    assert not api.calls("PUT", "/v1/datasets/ds1/schema")

    result = runner.invoke(app, ["schema", "push", "demo", str(path), "--yes"])
    assert result.exit_code == 0, result.output
    assert len(api.calls("PUT", "/v1/datasets/ds1/schema")) == 1


# --- rows pull ------------------------------------------------------------------


def test_rows_pull_writes_nested_records_and_state(env, install_transport):
    install_transport(FakeApi())
    result = runner.invoke(app, ["rows", "pull", "demo", "-o", "demo.yaml", "--records-key", "cases"])
    assert result.exit_code == 0, result.output

    doc = yaml.safe_load((env / "demo.yaml").read_text(encoding="utf-8"))
    assert doc == {"cases": [
        {"id": "a", "question": "qa", "expected": {"calls": [{"args": {"a": 2, "z": 1}, "name": "f"}]}},
        {"id": "b"},
    ]}
    text = (env / "demo.yaml").read_text(encoding="utf-8")
    assert text.index("a: 2") < text.index("z: 1")  # json keys sorted

    state = json.loads(state_path(env / "demo.yaml", "demo").read_text())
    assert state["dataset_id"] == "ds1" and state["key_column"] == "id"
    assert state["file"] == "demo.yaml"
    assert state["revs"] == {"a": 3, "b": 1}
    assert state["bases"] == {
        "a": row_hash(ROWS[0]["data"], KEYS), "b": row_hash(ROWS[1]["data"], KEYS),
    }


def test_rows_pull_into_an_existing_file_keeps_its_frame(env, install_transport):
    api = FakeApi(sync_result=_result(applied=False))
    install_transport(api)
    (env / "suite.yaml").write_text(
        "as_of: &d '2099-01-01'\nflag: NO\nowner: evals\n"
        "cases:\n  - {id: old, question: x, when: *d}\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["rows", "pull", "demo", "-o", "suite.yaml"])
    assert result.exit_code == 0, result.output

    text = (env / "suite.yaml").read_text(encoding="utf-8")
    assert "flag: NO\n" in text  # plain value kept as written, not 'NO' or false
    doc = yaml.safe_load(text)
    assert doc["as_of"] == "2099-01-01" and doc["owner"] == "evals" and doc["flag"] is False
    assert [r["id"] for r in doc["cases"]] == ["a", "b"]  # records key found in the file
    assert len(api.calls("POST", "/v1/datasets/ds1/sync")) == 1  # local-edit check ran


def test_rows_pull_refuses_to_overwrite_unpushed_edits(env, install_transport):
    edited = _result(applied=False, changes=[{"key": "a", "op": "update", "fields": {}}])
    api = FakeApi(sync_result=edited)
    install_transport(api)
    (env / "demo.yaml").write_text("- {id: a, question: mine}\n")

    result = runner.invoke(app, ["rows", "pull", "demo", "-o", "demo.yaml"])
    assert result.exit_code == 1
    assert "1 row(s) in demo.yaml changed locally" in result.output
    assert (env / "demo.yaml").read_text() == "- {id: a, question: mine}\n"
    assert _form(api.calls("POST", "/v1/datasets/ds1/sync")[0])["dry_run"] == "true"

    result = runner.invoke(app, ["rows", "pull", "demo", "-o", "demo.yaml", "--force"])
    assert result.exit_code == 0, result.output
    assert len(api.calls("POST", "/v1/datasets/ds1/sync")) == 1  # --force skips the check


# --- rows push ------------------------------------------------------------------


def test_rows_push_sends_state_and_refreshes_it(env, install_transport):
    api = FakeApi(sync_result=_result(
        updated=1, unchanged=1, server_newer=1, server_newer_keys=["b"],
        changes=[{"key": "a", "op": "update", "fields": {"question": {"before": "qa", "after": "qa2"}}}],
        revs={"a": 4}, bases={"a": "sha256:new"},
    ))
    install_transport(api)
    runner.invoke(app, ["rows", "pull", "demo", "-o", "demo.yaml"])
    before = json.loads(state_path(env / "demo.yaml", "demo").read_text())

    result = runner.invoke(app, ["rows", "push", "demo", "demo.yaml"])
    assert result.exit_code == 0, result.output
    assert "updated 1" in result.output and "question: 'qa' -> 'qa2'" in result.output
    assert "1 row(s) changed in Groundline since your last pull (b)" in result.output

    form = _form(api.calls("POST", "/v1/datasets/ds1/sync")[-1])
    assert json.loads(form["revs"]) == before["revs"]
    assert json.loads(form["bases"]) == before["bases"]
    after = json.loads(state_path(env / "demo.yaml", "demo").read_text())
    assert after["revs"] == {**before["revs"], "a": 4}
    assert after["bases"]["a"] == "sha256:new"
    assert after["bases"]["b"] == before["bases"]["b"]  # server-newer keeps its old state


def test_rows_push_conflict_exits_1_and_keeps_state(env, install_transport):
    conflict = {"key": "a", "row_id": "r1", "server_rev": 5, "your_rev": 3,
                "fields": {"question": {"server": "ui", "file": "mine"}}}
    install_transport(FakeApi(sync_result=_result(applied=False, conflicts=[conflict])))
    (env / "demo.yaml").write_text("- {id: a, question: mine}\n")

    result = runner.invoke(app, ["rows", "push", "demo", "demo.yaml"])
    assert result.exit_code == 1
    assert "! a (server rev 5, yours 3)" in result.output
    assert "question: Groundline 'ui' | file 'mine'" in result.output
    assert "push with --force to overwrite them" in result.output
    assert not state_path(env / "demo.yaml", "demo").exists()


def test_rows_push_dry_run_and_first_push_without_state(env, install_transport):
    api = FakeApi(sync_result=_result(applied=False, created=1))
    install_transport(api)
    (env / "new.yaml").write_text("- {id: c, question: q}\n")

    result = runner.invoke(app, ["rows", "push", "demo", "new.yaml", "--dry-run"])
    assert result.exit_code == 0
    assert "dry run: nothing applied" in result.output
    form = _form(api.calls("POST", "/v1/datasets/ds1/sync")[0])
    assert form["dry_run"] == "true" and "revs" not in form and "bases" not in form
    assert not state_path(env / "new.yaml", "demo").exists()

    api.sync_result = _result(created=1, revs={"c": 1}, bases={"c": "sha256:c"})
    result = runner.invoke(app, ["rows", "push", "demo", "new.yaml"])
    assert result.exit_code == 0, result.output
    state = json.loads(state_path(env / "new.yaml", "demo").read_text())
    assert state["revs"] == {"c": 1} and state["file"] == "new.yaml"


def test_rows_commands_need_a_key_column(env, install_transport):
    api = FakeApi()
    api.schema = [dict(c, is_key=False) for c in COLUMNS]
    install_transport(api)
    (env / "x.yaml").write_text("- {id: a}\n")
    for args in (["rows", "pull", "demo"], ["rows", "push", "demo", "x.yaml"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 1
        assert "has no key column" in result.output
