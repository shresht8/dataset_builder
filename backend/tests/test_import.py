"""GL-2-8: CSV/XLSX import preview + commit with column mapping (§4, §8)."""

from __future__ import annotations

import csv
import io
import json
import time
import uuid

import openpyxl
import pytest
from groundline_api.db import SessionLocal
from groundline_api.models.dataset import Dataset

SCHEMA_COLUMNS = [
    {"key": "input", "label": "Customer question", "type": "long_text", "required": True},
    {
        "key": "verdict",
        "label": "Correct?",
        "type": "select",
        "options": ["correct", "partially_correct", "incorrect"],
        "required": True,
    },
    {
        "key": "tags",
        "label": "Tags",
        "type": "multi_select",
        "options": ["billing", "eligibility", "fraud"],
    },
    {"key": "score", "label": "Score", "type": "number"},
    {"key": "flagged", "label": "Flagged", "type": "boolean"},
    {"key": "notes", "label": "Notes", "type": "text"},
]


@pytest.fixture()
def dataset_id(client, as_role):
    as_role("editor")
    resp = client.post("/v1/datasets", json={"name": f"gl28-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 201, resp.text
    ds_id = resp.json()["id"]
    resp = client.put(f"/v1/datasets/{ds_id}/schema", json={"columns": SCHEMA_COLUMNS})
    assert resp.status_code == 200, resp.text
    yield ds_id
    db = SessionLocal()
    try:
        ds = db.get(Dataset, uuid.UUID(ds_id))
        if ds is not None:
            db.delete(ds)  # columns + rows cascade via FK
            db.commit()
    finally:
        db.close()


def _csv_bytes(header: list[str], rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


def _xlsx_bytes(header: list[str], rows: list[list[str]]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


HEADER = ["Question", "Verdict", "Tags", "Score", "Flagged", "Notes"]
MAPPING = {
    "Question": "input",
    "Verdict": "verdict",
    "Tags": "tags",
    "Score": "score",
    "Flagged": "flagged",
    "Notes": "notes",
}
VALID_ROWS = [
    ["Is the customer eligible?", "correct", "billing, eligibility", "4.5", "true", "looks fine"],
    ["Was the refund processed?", "incorrect", "billing", "2", "no", ""],
]


def test_preview_csv_detects_columns_and_suggests_mapping(client, as_role, dataset_id):
    as_role("editor")
    content = _csv_bytes(HEADER, VALID_ROWS)
    resp = client.post(
        f"/v1/datasets/{dataset_id}/import/preview",
        files={"file": ("rows.csv", content, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["columns"] == HEADER
    # Case-insensitive auto-mapping onto schema keys; "Question" has no
    # case-insensitive match against the "input" key, so it's left unmapped.
    assert body["suggested_mapping"] == {
        "Verdict": "verdict",
        "Tags": "tags",
        "Score": "score",
        "Flagged": "flagged",
        "Notes": "notes",
    }
    assert body["total_rows"] == 2
    assert len(body["sample_rows"]) == 2
    assert body["sample_rows"][0]["row"] == 1
    assert body["sample_rows"][0]["values"]["Question"] == VALID_ROWS[0][0]
    assert body["validation"] is None


def test_preview_with_mapping_returns_validation_report(client, as_role, dataset_id):
    as_role("editor")
    rows = VALID_ROWS + [["Missing verdict", "", "", "", "", ""]]
    content = _csv_bytes(HEADER, rows)
    resp = client.post(
        f"/v1/datasets/{dataset_id}/import/preview",
        files={"file": ("rows.csv", content, "text/csv")},
        data={"mapping": json.dumps(MAPPING)},
    )
    assert resp.status_code == 200, resp.text
    validation = resp.json()["validation"]
    assert validation["valid"] == 2
    assert validation["errors"] == [
        {"row": 3, "column": "verdict", "reason": "required"},
    ]
    # Preview persists nothing.
    resp = client.get(f"/v1/datasets/{dataset_id}/rows")
    assert resp.json() == []


def test_import_commit_csv_creates_valid_rows_and_reports_invalid(client, as_role, dataset_id):
    as_role("editor")
    rows = VALID_ROWS + [["Bad score row", "correct", "billing", "not-a-number", "true", ""]]
    content = _csv_bytes(HEADER, rows)
    resp = client.post(
        f"/v1/datasets/{dataset_id}/import",
        files={"file": ("rows.csv", content, "text/csv")},
        data={"mapping": json.dumps(MAPPING)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["imported"] == 2
    assert body["skipped"] == 0
    assert body["errors"] == [
        {"row": 3, "column": "score", "reason": "expected a number, got str"}
    ]

    resp = client.get(f"/v1/datasets/{dataset_id}/rows")
    assert resp.status_code == 200
    created = {r["data"]["input"]: r["data"] for r in resp.json()}
    assert set(created) == {VALID_ROWS[0][0], VALID_ROWS[1][0]}
    row0 = created[VALID_ROWS[0][0]]
    assert row0["verdict"] == "correct"
    assert row0["tags"] == ["billing", "eligibility"]
    assert row0["score"] == 4.5
    assert row0["flagged"] is True
    row1 = created[VALID_ROWS[1][0]]
    assert row1["score"] == 2
    assert row1["flagged"] is False
    assert "notes" not in row1  # empty cell = absent


def test_import_commit_xlsx_creates_valid_rows(client, as_role, dataset_id):
    as_role("editor")
    content = _xlsx_bytes(HEADER, VALID_ROWS)
    resp = client.post(
        f"/v1/datasets/{dataset_id}/import",
        files={
            "file": (
                "rows.xlsx",
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        data={"mapping": json.dumps(MAPPING)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["imported"] == 2
    assert body["skipped"] == 0
    assert body["errors"] == []


def test_import_skip_and_fixes_account_for_every_row(client, as_role, dataset_id):
    as_role("editor")
    rows = [
        VALID_ROWS[0],
        ["Skip me", "correct", "billing", "1", "yes", ""],
        ["Fix me", "", "billing", "1", "yes", ""],  # missing verdict, fixed below
    ]
    content = _csv_bytes(HEADER, rows)
    resp = client.post(
        f"/v1/datasets/{dataset_id}/import",
        files={"file": ("rows.csv", content, "text/csv")},
        data={
            "mapping": json.dumps(MAPPING),
            "skip": json.dumps([2]),
            "fixes": json.dumps({"3": {"verdict": "incorrect"}}),
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["imported"] == 2
    assert body["skipped"] == 1
    assert body["errors"] == []


def test_import_unmapped_schema_key_422(client, as_role, dataset_id):
    as_role("editor")
    content = _csv_bytes(HEADER, VALID_ROWS)
    resp = client.post(
        f"/v1/datasets/{dataset_id}/import",
        files={"file": ("rows.csv", content, "text/csv")},
        data={"mapping": json.dumps({"Question": "does_not_exist"})},
    )
    assert resp.status_code == 422, resp.text
    assert "does_not_exist" in resp.json()["detail"]


def test_import_duplicate_mapping_target_422(client, as_role, dataset_id):
    as_role("editor")
    content = _csv_bytes(HEADER, VALID_ROWS)
    resp = client.post(
        f"/v1/datasets/{dataset_id}/import",
        files={"file": ("rows.csv", content, "text/csv")},
        data={"mapping": json.dumps({"Question": "input", "Notes": "input"})},
    )
    assert resp.status_code == 422, resp.text
    assert "input" in resp.json()["detail"]


def test_import_requires_editor_role(client, as_role, dataset_id):
    as_role("annotator")
    content = _csv_bytes(HEADER, VALID_ROWS)
    resp = client.post(
        f"/v1/datasets/{dataset_id}/import",
        files={"file": ("rows.csv", content, "text/csv")},
        data={"mapping": json.dumps(MAPPING)},
    )
    assert resp.status_code == 403, resp.text


def test_import_unsupported_file_type_422(client, as_role, dataset_id):
    as_role("editor")
    resp = client.post(
        f"/v1/datasets/{dataset_id}/import/preview",
        files={"file": ("rows.txt", b"a,b\n1,2", "text/plain")},
    )
    assert resp.status_code == 422, resp.text


def test_import_3000_rows_completes_quickly(client, as_role, dataset_id):
    as_role("editor")
    rows = [
        [f"Question {i}", "correct", "billing", str(i), "true", ""] for i in range(3000)
    ]
    content = _csv_bytes(HEADER, rows)

    start = time.perf_counter()
    resp = client.post(
        f"/v1/datasets/{dataset_id}/import",
        files={"file": ("rows.csv", content, "text/csv")},
        data={"mapping": json.dumps(MAPPING)},
    )
    elapsed = time.perf_counter() - start

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["imported"] == 3000
    assert body["skipped"] == 0
    assert body["errors"] == []
    print(f"\n3000-row import completed in {elapsed:.2f}s")
    assert elapsed < 15
