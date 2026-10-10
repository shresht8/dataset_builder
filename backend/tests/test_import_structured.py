"""GL-3.5-5: import JSON / JSONL / YAML — records, nesting, typed coercion,
safety, and the key column. Uses the synthetic eval-suite fixture (decision k).
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from pathlib import Path

import boto3
import openpyxl
import pytest
import yaml
from groundline_api.config import settings
from groundline_api.db import SessionLocal
from groundline_api.models.dataset import Dataset
from groundline_api.models.row import DatasetRow
from groundline_schema.paths import flatten, nest
from sqlalchemy import select

FIXTURE = Path(__file__).parent / "fixtures" / "eval_suite.yaml"
CALLS_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"name": {"type": "string"}, "args": {"type": "object"}},
        "required": ["name", "args"],
    },
}


def _cases() -> list[dict]:
    return yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))["cases"]


def _options(path: str) -> list[str]:
    return sorted({v for case in _cases() for v in flatten(case).get(path, [])})


def _suite_columns() -> list[dict]:
    return [
        {"key": "id", "label": "ID", "type": "text", "required": True, "is_key": True},
        {"key": "prompt", "label": "Prompt", "type": "text", "required": True},
        {"key": "title", "label": "Title", "type": "text"},
        {"key": "topic", "label": "Topic", "type": "text"},
        {"key": "step", "label": "Step", "type": "number"},
        {"key": "thread_id", "label": "Thread", "type": "text"},
        {"key": "expected.routes", "label": "Routes", "type": "multi_select",
         "options": _options("expected.routes")},
        {"key": "expected.tools", "label": "Tools", "type": "multi_select",
         "options": _options("expected.tools")},
        {"key": "expected.calls", "label": "Calls", "type": "json", "json_schema": CALLS_SCHEMA},
        {"key": "expected.answer", "label": "Answer", "type": "long_text", "required": True},
        {"key": "expected.has_text", "label": "Has text", "type": "boolean"},
        {"key": "expected.has_table", "label": "Has table", "type": "boolean"},
        {"key": "expected.match_mode", "label": "Match mode", "type": "text"},
        {"key": "expected.follow_up", "label": "Follow-up", "type": "boolean"},
    ]


def _drop_nulls(value):
    if isinstance(value, dict):
        return {k: _drop_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_drop_nulls(v) for v in value]
    return value


def _new_dataset(client, as_role, columns) -> str:
    as_role("editor")
    resp = client.post("/v1/datasets", json={"name": f"gl355-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 201, resp.text
    ds_id = resp.json()["id"]
    resp = client.put(f"/v1/datasets/{ds_id}/schema", json={"columns": columns})
    assert resp.status_code == 200, resp.text
    return ds_id


@pytest.fixture()
def datasets():
    created: list[str] = []
    yield created
    db = SessionLocal()
    try:
        for ds_id in created:
            ds = db.get(Dataset, uuid.UUID(ds_id))
            if ds is not None:
                db.delete(ds)
        db.commit()
    finally:
        db.close()


@pytest.fixture()
def suite_dataset(client, as_role, datasets):
    ds_id = _new_dataset(client, as_role, _suite_columns())
    datasets.append(ds_id)
    return ds_id


@pytest.fixture()
def simple_dataset(client, as_role, datasets):
    columns = [
        {"key": "name", "label": "Name", "type": "text"},
        {"key": "count", "label": "Count", "type": "number"},
        {"key": "flag", "label": "Flag", "type": "boolean"},
        {"key": "tags", "label": "Tags", "type": "multi_select", "options": ["a", "b"]},
        {"key": "blob", "label": "Blob", "type": "json"},
    ]
    ds_id = _new_dataset(client, as_role, columns)
    datasets.append(ds_id)
    return ds_id


def _preview(client, ds_id, filename, content, **form):
    data = {k: json.dumps(v) if not isinstance(v, str) else v for k, v in form.items()}
    return client.post(
        f"/v1/datasets/{ds_id}/import/preview", files={"file": (filename, content)}, data=data
    )


def _commit(client, ds_id, filename, content, mapping, **form):
    data = {"mapping": json.dumps(mapping)}
    data.update({k: json.dumps(v) if not isinstance(v, str) else v for k, v in form.items()})
    return client.post(
        f"/v1/datasets/{ds_id}/import", files={"file": (filename, content)}, data=data
    )


def _import(client, ds_id, filename, content, **form):
    """Preview for the suggested mapping, then commit with it."""
    preview = _preview(client, ds_id, filename, content, **form)
    assert preview.status_code == 200, preview.text
    resp = _commit(client, ds_id, filename, content, preview.json()["suggested_mapping"], **form)
    assert resp.status_code == 200, resp.text
    return preview.json(), resp.json()


def _rows(ds_id) -> list[DatasetRow]:
    db = SessionLocal()
    try:
        return list(
            db.scalars(
                select(DatasetRow)
                .where(DatasetRow.dataset_id == uuid.UUID(ds_id))
                .order_by(DatasetRow.created_at, DatasetRow.id)
            )
        )
    finally:
        db.close()


def _data_by_id(ds_id) -> dict[str, dict]:
    return {row.data["id"]: row.data for row in _rows(ds_id)}


# --- The fixture end to end ------------------------------------------------


def test_fixture_yaml_imports_cleanly(client, suite_dataset):
    content = FIXTURE.read_bytes()
    preview = _preview(client, suite_dataset, "suite.yaml", content).json()
    assert preview["records_key"] == "cases"
    assert preview["ignored_keys"] == ["as_of", "period_start", "prior_period_end", "suite_owner"]
    assert set(preview["suggested_mapping"].values()) == {c["key"] for c in _suite_columns()}
    assert preview["total_rows"] == len(_cases())

    preview_full = _preview(
        client, suite_dataset, "suite.yaml", content, mapping=preview["suggested_mapping"]
    ).json()
    assert preview_full["validation"] == {"valid": len(_cases()), "errors": []}

    result = _commit(client, suite_dataset, "suite.yaml", content, preview["suggested_mapping"]).json()
    assert result == {"imported": len(_cases()), "skipped": 0, "errors": []}

    stored = _data_by_id(suite_dataset)
    for case in _cases():
        assert _drop_nulls(nest(stored[case["id"]])) == _drop_nulls(case)
    sales = stored["case-sales-01"]
    assert sales["step"] == 2
    assert sales["expected.calls"][0]["args"]["to_date"] == "2099-03-31"  # alias, quoted
    assert sales["expected.calls"][0]["args"]["limit"] == 5
    assert sales["expected.calls"][1]["args"]["include_children"] is True
    assert stored["case-orders-01"]["expected.calls"][0]["args"] == {}
    assert stored["case-weather-01"]["expected.answer"].endswith("No real data here.\n")
    assert {row.row_key for row in _rows(suite_dataset)} == {c["id"] for c in _cases()}


def test_same_data_as_json_jsonl_and_csv_gives_identical_rows(client, as_role, datasets):
    columns = [c for c in _suite_columns() if c["type"] != "json"]
    flat_cases = [
        {k: v for k, v in flatten(case, {"expected.calls"}).items() if k != "expected.calls"}
        for case in _cases()
    ]
    jsonl = "".join(json.dumps(case) + "\n" for case in _cases()).encode()
    as_json = json.dumps({"cases": _cases()}).encode()

    csv_cases = [{k: v for k, v in c.items() if not isinstance(v, list)} for c in flat_cases]
    header = sorted({k for case in csv_cases for k in case})
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=header)
    writer.writeheader()
    for case in csv_cases:
        writer.writerow({k: ("" if v is None else str(v).lower() if isinstance(v, bool) else v)
                         for k, v in case.items()})

    results = {}
    for filename, content in [
        ("s.yaml", FIXTURE.read_bytes()), ("s.json", as_json),
        ("s.jsonl", jsonl), ("s.csv", buffer.getvalue().encode()),
    ]:
        ds_id = _new_dataset(client, as_role, columns)
        datasets.append(ds_id)
        _import(client, ds_id, filename, content)
        results[filename] = _data_by_id(ds_id)

    shared = set(header)
    def project(rows):
        return {k: {c: v for c, v in d.items() if c in shared} for k, d in rows.items()}
    assert project(results["s.json"]) == project(results["s.yaml"])
    assert project(results["s.jsonl"]) == project(results["s.yaml"])
    assert project(results["s.csv"]) == project(results["s.yaml"])


@pytest.fixture(scope="module")
def storage_bucket():
    bucket = f"gl355-test-{uuid.uuid4().hex[:8]}"
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


@pytest.mark.parametrize("fmt", ["jsonl", "json", "yaml"])
@pytest.mark.parametrize("shape", ["flat", "nested"])
def test_reimporting_an_export_gives_identical_data(
    client, as_role, datasets, suite_dataset, storage_bucket, fmt, shape
):
    _import(client, suite_dataset, "suite.yaml", FIXTURE.read_bytes())
    resp = client.post(
        f"/v1/datasets/{suite_dataset}/versions", json={"include_unapproved": True}
    )
    assert resp.status_code == 201, resp.text
    v = resp.json()["version"]
    export = client.get(
        f"/v1/datasets/{suite_dataset}/versions/{v}", params={"format": fmt, "shape": shape}
    ).content

    target = _new_dataset(client, as_role, _suite_columns())
    datasets.append(target)
    _, result = _import(client, target, f"export.{fmt}", export)
    assert result["errors"] == []
    assert _data_by_id(target) == _data_by_id(suite_dataset)
    assert {row.status.value for row in _rows(target)} == {"draft"}


# --- Key column -------------------------------------------------------------


def test_key_duplicates_in_file_and_existing_keys_are_row_errors(client, suite_dataset):
    content = FIXTURE.read_bytes()
    _import(client, suite_dataset, "suite.yaml", content)

    again = _preview(client, suite_dataset, "suite.yaml", content)
    mapping = again.json()["suggested_mapping"]
    report = _preview(client, suite_dataset, "suite.yaml", content, mapping=mapping).json()
    assert report["validation"]["valid"] == 0
    assert all("already exists — use rows push to update" in e["reason"]
               for e in report["validation"]["errors"])

    doubled = yaml.safe_load(content)
    doubled["cases"] = [dict(doubled["cases"][0], id="new-1")] * 2
    doubled_bytes = yaml.safe_dump(doubled).encode()
    result = _commit(client, suite_dataset, "d.yaml", doubled_bytes, mapping).json()
    assert result["imported"] == 1
    assert result["errors"] == [
        {"row": 2, "column": "id", "reason": "duplicate key 'new-1' in file (also row 1)"}
    ]
    assert "new-1" in {row.row_key for row in _rows(suite_dataset)}


# --- YAML rules -------------------------------------------------------------

MAPPING = {"name": "name", "count": "count", "flag": "flag", "tags": "tags", "blob": "blob"}


def test_yaml_plain_scalars_keep_text_in_text_columns(client, simple_dataset):
    content = b"- {name: NO, count: 012}\n- {name: '012', count: 5}\n"
    _commit(client, simple_dataset, "x.yaml", content, MAPPING)
    data = [row.data for row in _rows(simple_dataset)]
    assert data[0]["name"] == "NO" and data[0]["count"] == 12
    assert data[1] == {"name": "012", "count": 5}


def test_yaml_json_column_typing(client, simple_dataset):
    content = b"- blob: {a: yes, b: NO, c: '5', d: 5, e: 012, f: true, g: 2099-01-02, h: 1.5e3}\n"
    result = _commit(client, simple_dataset, "x.yaml", content, MAPPING).json()
    assert result["errors"] == []
    assert _rows(simple_dataset)[0].data["blob"] == {
        "a": "yes", "b": "NO", "c": "5", "d": 5, "e": "012", "f": True,
        "g": "2099-01-02", "h": 1500.0,
    }


def test_yaml_nan_in_json_column_is_a_row_error(client, simple_dataset):
    content = b"- {name: ok, blob: {x: .nan}}\n- {name: fine}\n"
    result = _commit(client, simple_dataset, "x.yaml", content, MAPPING).json()
    assert result["imported"] == 1
    assert result["errors"][0]["row"] == 1
    assert "not representable in JSON" in result["errors"][0]["reason"]


def test_yaml_merge_keys_and_aliases(client, simple_dataset):
    content = b"base: &b {count: 3, flag: true}\nrows:\n  - {<<: *b, name: one}\n  - {<<: *b, name: two, count: 4}\n"
    preview = _preview(client, simple_dataset, "x.yaml", content).json()
    assert preview["records_key"] == "rows"
    _commit(client, simple_dataset, "x.yaml", content, MAPPING)
    assert [row.data for row in _rows(simple_dataset)] == [
        {"name": "one", "count": 3, "flag": True},
        {"name": "two", "count": 4, "flag": True},
    ]


@pytest.mark.parametrize(
    "content, message",
    [
        (b"- !!python/object:os.system {name: x}\n", "YAML tags are not allowed"),
        (b"- !!binary aGk=\n", "YAML tags are not allowed"),
        (b"- {name: a}\n---\n- {name: b}\n", "multi-document YAML"),
        (b"- {name: [unclosed\n", "invalid YAML"),
    ],
)
def test_yaml_refusals(client, simple_dataset, content, message):
    resp = _preview(client, simple_dataset, "x.yaml", content)
    assert resp.status_code == 422
    assert message in resp.json()["detail"]


def test_yaml_alias_bomb_refused(client, simple_dataset, monkeypatch):
    monkeypatch.setattr(settings, "import_max_nodes", 1000)
    lines = ["a0: &a0 [x, x, x, x, x, x, x, x, x, x]"]
    for i in range(1, 6):
        prev = f"*a{i - 1}"
        lines.append(f"a{i}: &a{i} [{', '.join([prev] * 10)}]")
    lines.append("rows: [{name: *a5}]")
    resp = _preview(client, simple_dataset, "bomb.yaml", "\n".join(lines).encode())
    assert resp.status_code == 422
    assert "more than 1000 values" in resp.json()["detail"]


def test_yaml_self_referencing_alias_refused(client, simple_dataset):
    resp = _preview(client, simple_dataset, "x.yaml", b"rows: &r [{name: x, tags: *r}]\n")
    assert resp.status_code == 422
    assert "refers to itself" in resp.json()["detail"]


def test_duplicate_keys_are_row_errors(client, simple_dataset):
    content = b'[{"name": "a", "name": "b"}, {"name": "ok"}]'
    result = _commit(client, simple_dataset, "x.json", content, MAPPING).json()
    assert result["imported"] == 1
    assert result["errors"][0]["reason"] == "duplicate key 'name'"

    content = b"- {name: a, name: b}\n- {name: ok}\n"
    result = _commit(client, simple_dataset, "y.yaml", content, MAPPING).json()
    assert result["errors"][0]["reason"] == "duplicate key 'name'"


def test_path_collision_is_a_row_error(client, simple_dataset):
    content = b'[{"a.b": 1, "a": {"b": 2}}, {"name": "ok"}]'
    result = _commit(client, simple_dataset, "x.json", content, MAPPING).json()
    assert result["imported"] == 1
    assert "path 'a.b' appears twice" in result["errors"][0]["reason"]


# --- JSON / JSONL -----------------------------------------------------------


def test_json_nan_refused(client, simple_dataset):
    resp = _preview(client, simple_dataset, "x.json", b'[{"count": NaN}]')
    assert resp.status_code == 422
    assert "NaN is not valid JSON" in resp.json()["detail"]


def test_json_that_is_really_jsonl_gets_a_hint(client, simple_dataset):
    resp = _preview(client, simple_dataset, "x.json", b'{"name": "a"}\n{"name": "b"}\n')
    assert resp.status_code == 422
    assert "save it as .jsonl" in resp.json()["detail"]


def test_malformed_jsonl_line_and_non_object_are_row_errors(client, simple_dataset):
    content = b'{"name": "a"}\n\n{"name": \n[1, 2]\n{"name": "d"}\n'
    result = _commit(client, simple_dataset, "x.jsonl", content, MAPPING).json()
    assert result["imported"] == 2
    reasons = {e["row"]: e["reason"] for e in result["errors"]}
    assert reasons[2].startswith("line 3: invalid JSON")
    assert reasons[3] == "record is not an object"


def test_records_key_required_lists_candidates(client, simple_dataset):
    content = b'{"one": [{"name": "a"}], "two": [{"name": "b"}]}'
    resp = _preview(client, simple_dataset, "x.json", content)
    assert resp.status_code == 422
    assert resp.json()["records_key_candidates"] == ["one", "two"]

    preview, _ = _import(client, simple_dataset, "x.json", content, records_key="two")
    assert preview["records_key"] == "two" and preview["ignored_keys"] == ["one"]
    assert [row.data for row in _rows(simple_dataset)] == [{"name": "b"}]


def test_groundline_lines_are_unwrapped(client, simple_dataset):
    content = b'{"id": "r1", "status": "approved", "data": {"name": "x", "count": 2}}\n'
    _import(client, simple_dataset, "x.jsonl", content)
    rows = _rows(simple_dataset)
    assert rows[0].data == {"name": "x", "count": 2}
    assert rows[0].status.value == "draft"


@pytest.mark.parametrize("filename, content", [
    ("x.json", b""), ("x.json", b"[]"), ("x.jsonl", b"\n\n"), ("x.yaml", b""), ("x.yaml", b"rows: []\n"),
])
def test_empty_files_import_zero_rows(client, simple_dataset, filename, content):
    resp = _preview(client, simple_dataset, filename, content)
    assert resp.status_code == 200, resp.text
    assert resp.json()["total_rows"] == 0


# --- Coercion table ---------------------------------------------------------


def test_coercion_of_typed_values(client, simple_dataset):
    content = json.dumps([
        {"name": 5, "count": "7", "flag": 1, "tags": ["a"], "blob": "text"},
        {"name": True, "flag": 0},
        {"name": ["x"]},
        {"count": {}},
    ]).encode()
    result = _commit(client, simple_dataset, "x.json", content, MAPPING).json()
    assert result["imported"] == 2
    data = [row.data for row in _rows(simple_dataset)]
    assert data[0] == {"name": "5", "count": 7, "flag": True, "tags": ["a"], "blob": "text"}
    assert data[1] == {"name": "true", "flag": False}
    reasons = {(e["row"], e["column"]): e["reason"] for e in result["errors"]}
    assert reasons[(3, "name")] == "map this to a json column"
    assert reasons[(4, "count")] == "map this to a json column"


def test_nested_object_under_a_non_json_column_becomes_dotted_columns(client, simple_dataset):
    preview = _preview(client, simple_dataset, "x.json", b'[{"count": {"a": 1}}]').json()
    assert preview["columns"] == ["count.a"]
    assert preview["suggested_mapping"] == {}


# --- Safety -----------------------------------------------------------------


def test_upload_cap_applies_to_every_format(client, simple_dataset, monkeypatch):
    monkeypatch.setattr(settings, "import_max_bytes", 10)
    for filename in ("x.csv", "x.json", "x.yaml"):
        resp = _preview(client, simple_dataset, filename, b"name\n" + b"a" * 20)
        assert resp.status_code == 413
        assert "import limit" in resp.json()["detail"]


def test_non_utf8_csv_and_json_are_422(client, simple_dataset):
    resp = _preview(client, simple_dataset, "x.csv", "name\ncafé – x\n".encode("cp1252"))
    assert resp.status_code == 422
    assert resp.json()["detail"] == 'file is not UTF-8 — save it as "CSV UTF-8"'
    resp = _preview(client, simple_dataset, "x.json", '[{"name": "café"}]'.encode("cp1252"))
    assert resp.status_code == 422
    assert resp.json()["detail"] == "file is not UTF-8"


def test_bom_is_accepted(client, simple_dataset):
    resp = _preview(client, simple_dataset, "x.json", '﻿[{"name": "a"}]'.encode())
    assert resp.status_code == 200, resp.text


def test_unreadable_xlsx_is_422(client, simple_dataset):
    resp = _preview(client, simple_dataset, "x.xlsx", b"not a workbook")
    assert resp.status_code == 422
    assert "could not read the workbook" in resp.json()["detail"]


def test_valid_xlsx_still_imports(client, simple_dataset):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["name", "count"])
    sheet.append(["a", 3])
    buffer = io.BytesIO()
    workbook.save(buffer)
    _import(client, simple_dataset, "x.xlsx", buffer.getvalue())
    assert _rows(simple_dataset)[0].data == {"name": "a", "count": 3}
