"""GL-147: files that can't be read get plain messages; header-only files draft a schema."""

from __future__ import annotations

import json

import pytest
from groundline_schema.records import NOT_RECORDS, RecordsError, parse_records

BAD_PATH = "invalid path"


def _infer(client, name: str, content: bytes):
    return client.post("/v1/schema/infer", files={"file": (name, content)})


@pytest.fixture()
def editor(as_role):
    as_role("editor")


def test_nameless_column_between_names(client, editor):
    resp = _infer(client, "data.csv", b"id,question,,score\nx1,a,c,0.1\n")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail.startswith("column 3 has no name")
    assert BAD_PATH not in detail


def test_repeated_column_name_is_named(client, editor):
    resp = _infer(client, "data.csv", b"id,question,question,score\nx1,a,b,0.1\n")
    assert resp.status_code == 422
    assert '"question" is used more than once' in resp.json()["detail"]


def test_repeated_name_also_stops_import_preview(client, editor):
    columns = [{"key": "id", "label": "ID", "type": "text"}]
    ds = client.post(
        "/v1/datasets", json={"name": "gl147-dup", "columns": columns}
    ).json()
    try:
        resp = client.post(
            f"/v1/datasets/{ds['id']}/import/preview",
            files={"file": ("data.csv", b"id,id\na,b\n")},
        )
        assert resp.status_code == 422
        assert '"id" is used more than once' in resp.json()["detail"]
    finally:
        client.delete(f"/v1/datasets/{ds['id']}")


def test_title_rows_point_at_the_header(client, editor):
    content = b"Report generated 2026-01-01,,,\nExport by Acme,,,\n,,,\nid,question,a,score\nt1,q,a,0.5\n"
    resp = _infer(client, "data.csv", content)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail.startswith("column 2 has no name")
    assert "title rows" in detail


def test_trailing_empty_header_cells_without_data_are_dropped(client, editor):
    resp = _infer(client, "data.csv", b"id,question,\nx1,a,\n")
    assert resp.status_code == 200, resp.text
    assert [c["key"] for c in resp.json()["columns"]] == ["id", "question"]


def test_empty_file(client, editor):
    resp = _infer(client, "data.csv", b"")
    assert resp.status_code == 422
    assert resp.json()["detail"].startswith("this file is empty")


def test_header_only_drafts_text_columns(client, editor):
    resp = _infer(client, "data.csv", b"id,question,expected_answer\n")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["record_count"] == 0
    assert [(c["key"], c["label"], c["type"]) for c in body["columns"]] == [
        ("id", "Id", "text"),
        ("question", "Question", "text"),
        ("expected_answer", "Expected answer", "text"),
    ]
    resp = client.post(
        "/v1/datasets", json={"name": "gl147-header-only", "columns": body["columns"]}
    )
    assert resp.status_code == 201, resp.text
    client.delete(f"/v1/datasets/{resp.json()['id']}")


def test_single_object_is_refused_without_candidates(client, editor):
    doc = {
        "id": "j1",
        "question": "Q1",
        "tags": ["faq", "edge"],
        "meta": {"turns": [{"role": "user"}]},
    }
    resp = _infer(client, "data.json", json.dumps(doc).encode())
    assert resp.status_code == 422
    body = resp.json()
    assert body["detail"] == NOT_RECORDS
    assert not body.get("records_key_candidates")


def test_two_object_lists_still_ask():
    doc = {"train": [{"id": "a"}], "test": [{"id": "b"}], "tags": ["x"]}
    with pytest.raises(RecordsError) as info:
        parse_records("data.json", json.dumps(doc).encode())
    assert info.value.candidates == ["train", "test"]


def test_broken_json_names_line_and_column(client, editor):
    resp = _infer(client, "data.json", b'{"a": [1,2,')
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail.startswith(
        "this file is not valid JSON: the problem is at line 1, column 12"
    )
    assert "Expecting" not in detail and "char" not in detail


def test_broken_jsonl_line():
    parsed = parse_records("data.jsonl", b'{"id":"b1"}\n{bad json}\n')
    problem = parsed.records[1]
    assert problem.reason.startswith("line 2: not valid JSON at column 2.")
    assert "Expecting" not in problem.reason
