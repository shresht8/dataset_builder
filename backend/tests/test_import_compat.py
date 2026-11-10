"""GL-133: import compatibility checks (empty mapping, required column without a source,
compatibility report, blank lines, capped error report)."""

from __future__ import annotations

import csv
import io
import json
import uuid

import openpyxl
import pytest
from groundline_api.db import SessionLocal
from groundline_api.models.dataset import Dataset

REQUIRED_SCHEMA = [
    {"key": "question", "label": "Question", "type": "text", "required": True},
    {"key": "answer", "label": "Answer", "type": "number"},
    {"key": "notes", "label": "Notes", "type": "text"},
]
OPTIONAL_SCHEMA = [
    {"key": "title", "label": "Title", "type": "text"},
    {"key": "score", "label": "Score", "type": "number"},
]


def _make_dataset(client, columns):
    resp = client.post("/v1/datasets", json={"name": f"gl133-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 201, resp.text
    ds_id = resp.json()["id"]
    resp = client.put(f"/v1/datasets/{ds_id}/schema", json={"columns": columns})
    assert resp.status_code == 200, resp.text
    return ds_id


def _drop(ds_id):
    db = SessionLocal()
    try:
        ds = db.get(Dataset, uuid.UUID(ds_id))
        if ds is not None:
            db.delete(ds)
            db.commit()
    finally:
        db.close()


@pytest.fixture()
def required_ds(client, as_role):
    as_role("editor")
    ds_id = _make_dataset(client, REQUIRED_SCHEMA)
    yield ds_id
    _drop(ds_id)


@pytest.fixture()
def optional_ds(client, as_role):
    as_role("editor")
    ds_id = _make_dataset(client, OPTIONAL_SCHEMA)
    yield ds_id
    _drop(ds_id)


def _csv(header, rows, raw_lines=None) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _post_preview(client, ds, content, mapping=None, name="f.csv"):
    data = {} if mapping is None else {"mapping": json.dumps(mapping)}
    return client.post(
        f"/v1/datasets/{ds}/import/preview", files={"file": (name, content, "text/csv")}, data=data
    )


def _post_commit(client, ds, content, mapping, skip=None, name="f.csv"):
    data = {"mapping": json.dumps(mapping)}
    if skip is not None:
        data["skip"] = json.dumps(skip)
    return client.post(
        f"/v1/datasets/{ds}/import", files={"file": (name, content, "text/csv")}, data=data
    )


QUESTIONS = _csv(["q", "a", "d"], [["2+2", "4", "easy"], ["France", "Paris", "easy"], ["Boil", "100", "m"]])


# --- empty mapping (case A) ---------------------------------------------------


def test_empty_mapping_refused_on_preview_and_commit(client, as_role, optional_ds):
    as_role("editor")
    resp = _post_preview(client, optional_ds, QUESTIONS, mapping={})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "no column of the file is mapped to the schema"
    resp = _post_commit(client, optional_ds, QUESTIONS, mapping={})
    assert resp.status_code == 422, resp.text
    assert client.get(f"/v1/datasets/{optional_ds}/rows").json() == []


def test_preview_without_mapping_still_works_when_nothing_matches(client, as_role, optional_ds):
    as_role("editor")
    resp = _post_preview(client, optional_ds, QUESTIONS)
    assert resp.status_code == 200, resp.text
    assert resp.json()["suggested_mapping"] == {}


# --- required column without a source -------------------------------------------


def test_required_column_without_source_refused(client, as_role, required_ds):
    as_role("editor")
    mapping = {"a": "answer"}  # 'question' is required and has no source
    resp = _post_preview(client, required_ds, QUESTIONS, mapping=mapping)
    assert resp.status_code == 422, resp.text
    assert "'question'" in resp.json()["detail"]
    resp = _post_commit(client, required_ds, QUESTIONS, mapping)
    assert resp.status_code == 422, resp.text
    assert "'question'" in resp.json()["detail"]
    assert client.get(f"/v1/datasets/{required_ds}/rows").json() == []


# --- compatibility report ---------------------------------------------------------


def test_preview_report_lists_matched_ignored_and_unfilled(client, as_role, required_ds):
    as_role("editor")
    mapping = {"q": "question", "a": "answer"}
    resp = _post_preview(client, required_ds, QUESTIONS, mapping=mapping)
    assert resp.status_code == 200, resp.text
    compat = resp.json()["compatibility"]
    assert compat["matched"] == ["q", "a"]
    assert compat["ignored_source_columns"] == ["d"]
    assert compat["unfilled_schema_columns"] == ["notes"]
    assert compat["required_without_source"] == []


def test_preview_report_without_mapping_uses_the_suggested_one(client, as_role, required_ds):
    as_role("editor")
    resp = _post_preview(client, required_ds, QUESTIONS)
    assert resp.status_code == 200, resp.text
    compat = resp.json()["compatibility"]
    assert compat["matched"] == []
    assert compat["required_without_source"] == ["question"]


# --- blank lines --------------------------------------------------------------------


def test_blank_csv_lines_are_skipped_and_counted(client, as_role, optional_ds):
    as_role("editor")
    content = b"Title,Score\nfirst,1\n\nsecond,x\n"
    mapping = {"Title": "title", "Score": "score"}
    preview = _post_preview(client, optional_ds, content, mapping=mapping)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["total_rows"] == 3  # the blank line keeps its position
    assert body["blank_skipped"] == 1
    assert body["validation"]["valid"] == 1
    assert [e["row"] for e in body["validation"]["errors"]] == [3]
    assert [r["row"] for r in body["sample_rows"]] == [1, 3]

    # Round one: the row with the error stays out; blank line counted apart.
    first = _post_commit(client, optional_ds, content, mapping)
    assert first.status_code == 200, first.text
    r1 = first.json()
    assert (r1["imported"], r1["skipped"], r1["blank_skipped"]) == (1, 0, 1)
    assert [e["row"] for e in r1["errors"]] == [3]

    # Round two as the web sends it: indices 1 (imported) and 2 (blank) are in
    # `skip`, plus the user's own skip of row 3. Blanks never count as skipped.
    second = _post_commit(client, optional_ds, content, mapping, skip=[1, 2, 3])
    r2 = second.json()
    assert (r2["imported"], r2["skipped"], r2["blank_skipped"]) == (0, 2, 1)
    assert len(client.get(f"/v1/datasets/{optional_ds}/rows").json()) == 1


def test_blank_xlsx_rows_are_skipped(client, as_role, optional_ds):
    as_role("editor")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Title", "Score"])
    ws.append(["a", 1])
    ws.append([None, None])
    ws.append(["b", 2])
    buf = io.BytesIO()
    wb.save(buf)
    mapping = {"Title": "title", "Score": "score"}
    resp = client.post(
        f"/v1/datasets/{optional_ds}/import",
        files={"file": ("f.xlsx", buf.getvalue(), "application/octet-stream")},
        data={"mapping": json.dumps(mapping)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["imported"], body["skipped"], body["blank_skipped"]) == (2, 0, 1)


def test_row_with_data_but_nothing_in_mapped_columns_is_a_review_error(client, as_role, optional_ds):
    as_role("editor")
    content = b"Title,Other\nhello,x\n,only-other\n"
    mapping = {"Title": "title"}
    preview = _post_preview(client, optional_ds, content, mapping=mapping)
    body = preview.json()
    assert body["blank_skipped"] == 0
    assert body["validation"]["valid"] == 1
    assert body["validation"]["errors"] == [
        {"row": 2, "column": "", "reason": "no values in the mapped columns"}
    ]
    # The usual Skip tick clears it.
    resp = _post_commit(client, optional_ds, content, mapping, skip=[2])
    assert resp.json()["errors"] == []
    assert resp.json()["skipped"] == 1


def test_empty_json_record_is_a_real_record(client, as_role, optional_ds):
    as_role("editor")
    content = b'[{"title": "a"}, {}]'
    resp = client.post(
        f"/v1/datasets/{optional_ds}/import",
        files={"file": ("f.json", content, "application/json")},
        data={"mapping": json.dumps({"title": "title"})},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["imported"], body["blank_skipped"], body["errors"]) == (2, 0, [])


# --- capped error report ---------------------------------------------------------------


def test_preview_caps_the_error_list_but_reports_totals(client, as_role, optional_ds):
    as_role("editor")
    rows = [[f"t{i}", "nope"] for i in range(250)] + [["ok", "1"]]
    content = _csv(["Title", "Score"], rows)
    resp = _post_preview(client, optional_ds, content, mapping={"Title": "title", "Score": "score"})
    assert resp.status_code == 200, resp.text
    v = resp.json()["validation"]
    assert v["valid"] == 1
    assert len(v["errors"]) == 200
    assert v["error_total"] == 250
    assert v["failed_rows"] == 250
    assert v["checked_rows"] == 251
    assert v["by_column"] == [
        {"column": "score", "rows": 250, "reason": '"nope" is not a number'}
    ]
