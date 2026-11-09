"""GL-146: warnings before commit about dropped cells and repeated or missing IDs."""

from __future__ import annotations

import io
import json
import uuid

import openpyxl
import pytest
from groundline_api.db import SessionLocal
from groundline_api.models.dataset import Dataset

NO_KEY_COLUMNS = [
    {"key": "id", "label": "ID", "type": "text"},
    {"key": "question", "label": "Question", "type": "text"},
]
KEY_COLUMNS = [
    {"key": "id", "label": "ID", "type": "text", "required": True, "is_key": True},
    {"key": "question", "label": "Question", "type": "text"},
]
MAPPING = json.dumps({"id": "id", "question": "question"})

# Like 08_dirty.csv: extra cells on row 3, `a` twice, row 5 without an id,
# and a blank line (row 4) that is neither a record nor a missing id.
DIRTY = b"id,question\na,one\nb,two\nc,three,extra,extra2\n,,\na,again\n,no id\n"
CLEAN = b"id,question\na,one\nb,two\n"


def _dataset(client, columns):
    resp = client.post(
        "/v1/datasets",
        json={"name": f"gl146-{uuid.uuid4().hex[:8]}", "columns": columns},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest.fixture()
def make_dataset(client, as_role):
    as_role("editor")
    made: list[str] = []

    def make(columns):
        made.append(_dataset(client, columns))
        return made[-1]

    yield make
    db = SessionLocal()
    try:
        for ds_id in made:
            ds = db.get(Dataset, uuid.UUID(ds_id))
            if ds is not None:
                db.delete(ds)
        db.commit()
    finally:
        db.close()


def _validation(client, dataset_id, content, filename="data.csv"):
    resp = client.post(
        f"/v1/datasets/{dataset_id}/import/preview",
        files={"file": (filename, content)},
        data={"mapping": MAPPING},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["validation"]


def test_dirty_file_warns_about_extra_cells_and_ids(client, make_dataset):
    validation = _validation(client, make_dataset(NO_KEY_COLUMNS), DIRTY)
    assert validation["extra_cells"] == {"rows": 1, "examples": [3]}
    assert validation["id_check"] == {
        "column": "id",
        "repeated": ["a"],
        "repeated_rows": 1,
        "missing_rows": 1,
    }
    # Warnings only: every non-blank row is still valid.
    assert validation["valid"] == 5
    assert validation["failed_rows"] == 0


def test_clean_file_has_no_warnings(client, make_dataset):
    validation = _validation(client, make_dataset(NO_KEY_COLUMNS), CLEAN)
    assert validation["extra_cells"] is None
    assert validation["id_check"] is None


def test_import_goes_ahead_with_the_same_rows(client, make_dataset):
    dataset_id = make_dataset(NO_KEY_COLUMNS)
    resp = client.post(
        f"/v1/datasets/{dataset_id}/import",
        files={"file": ("data.csv", DIRTY)},
        data={"mapping": MAPPING},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["imported"] == 5
    rows = client.get(f"/v1/datasets/{dataset_id}/rows").json()
    assert sorted(r["data"].get("question") for r in rows) == [
        "again",
        "no id",
        "one",
        "three",
        "two",
    ]


def test_key_column_keeps_row_errors_and_no_id_warning(client, make_dataset):
    validation = _validation(client, make_dataset(KEY_COLUMNS), DIRTY)
    assert validation["id_check"] is None
    assert validation["failed_rows"] == 2  # the repeated `a` and the row without an id
    assert validation["extra_cells"] == {"rows": 1, "examples": [3]}


def test_no_id_check_when_id_is_not_mapped(client, make_dataset):
    dataset_id = make_dataset(NO_KEY_COLUMNS)
    resp = client.post(
        f"/v1/datasets/{dataset_id}/import/preview",
        files={"file": ("data.csv", DIRTY)},
        data={"mapping": json.dumps({"question": "question"})},
    )
    assert resp.json()["validation"]["id_check"] is None


def test_xlsx_cells_past_the_header(client, make_dataset):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["id", "question"])
    sheet.append(["a", "one"])
    sheet.append(["b", "two", None, "extra"])
    buf = io.BytesIO()
    book.save(buf)
    validation = _validation(
        client, make_dataset(NO_KEY_COLUMNS), buf.getvalue(), "data.xlsx"
    )
    assert validation["extra_cells"] == {"rows": 1, "examples": [2]}


def test_infer_explains_why_id_is_not_the_key(client, as_role):
    as_role("editor")
    resp = client.post("/v1/schema/infer", files={"file": ("data.csv", DIRTY)})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["record_count"] == 5
    assert (
        "'id' can't be the key: 1 record without one; repeated values 'a'"
        in body["warnings"]
    )


def test_infer_skips_blank_lines(client, as_role):
    as_role("editor")
    resp = client.post(
        "/v1/schema/infer", files={"file": ("data.csv", b"id,q\na,1\n\nb,2\n")}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["record_count"] == 2
    assert next(c for c in body["columns"] if c["key"] == "id").get("is_key") is True
    assert body["warnings"] == []


def test_why_not_key_names_each_reason():
    from groundline_schema.infer import NO_KEY, infer_columns

    _, warnings = infer_columns([{"id": 1}, {"id": " b "}, {"id": "c"}, {"id": "c"}])
    assert warnings == [
        NO_KEY,
        "'id' can't be the key: repeated values 'c'; 1 value not text; 1 value with leading or trailing spaces",
    ]
    _, warnings = infer_columns([{"q": "x"}])
    assert warnings == [NO_KEY]  # no `id` field: nothing to explain
