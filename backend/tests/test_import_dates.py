"""GL-3.5-9: import coercion for `date` columns, every format."""

# Spreadsheet cells carry no timezone, so these tests build naive datetimes on purpose.
# ruff: noqa: DTZ001

from __future__ import annotations

import datetime as dt
import io
import json
import uuid

import openpyxl
import pytest
from groundline_api.db import SessionLocal
from groundline_api.models.dataset import Dataset
from groundline_api.models.row import DatasetRow
from openpyxl.utils.datetime import CALENDAR_MAC_1904
from sqlalchemy import select

COLUMNS = [
    {"key": "name", "label": "Name", "type": "text"},
    {"key": "as_of", "label": "As of", "type": "date"},
    {"key": "note", "label": "Note", "type": "text"},
]
MAPPING = {"name": "name", "as_of": "as_of", "note": "note"}


@pytest.fixture()
def dataset_id(client, as_role):
    as_role("editor")
    resp = client.post("/v1/datasets", json={"name": f"gl359-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 201, resp.text
    ds_id = resp.json()["id"]
    assert client.put(f"/v1/datasets/{ds_id}/schema", json={"columns": COLUMNS}).status_code == 200
    yield ds_id
    db = SessionLocal()
    try:
        ds = db.get(Dataset, uuid.UUID(ds_id))
        if ds is not None:
            db.delete(ds)
            db.commit()
    finally:
        db.close()


def _commit(client, ds_id, filename, content, fixes=None):
    data = {"mapping": json.dumps(MAPPING)}
    if fixes:
        data["fixes"] = json.dumps(fixes)
    resp = client.post(f"/v1/datasets/{ds_id}/import", files={"file": (filename, content)}, data=data)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _rows(ds_id) -> list[dict]:
    db = SessionLocal()
    try:
        rows = db.scalars(
            select(DatasetRow)
            .where(DatasetRow.dataset_id == uuid.UUID(ds_id))
            .order_by(DatasetRow.created_at)
        )
        return [row.data for row in rows]
    finally:
        db.close()


def _reasons(result) -> dict[int, str]:
    return {e["row"]: e["reason"] for e in result["errors"]}


def _xlsx(rows: list[list], epoch=None) -> bytes:
    workbook = openpyxl.Workbook()
    if epoch is not None:
        workbook.epoch = epoch
    sheet = workbook.active
    sheet.append(["name", "as_of", "note"])
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_csv_strings(client, dataset_id):
    content = (
        b"name,as_of,note\n"
        b"ok,2024-02-29,\n"
        b"datetime,2024-01-02T03:04:05Z,\n"
        b"spaced,2024-01-02 03:04,\n"
        b"us,01/02/2024,\n"
        b"words,Jan 2 2024,\n"
    )
    result = _commit(client, dataset_id, "d.csv", content)
    assert result["imported"] == 1
    assert _rows(dataset_id) == [{"name": "ok", "as_of": "2024-02-29"}]
    reasons = _reasons(result)
    assert "has a time component" in reasons[2]
    assert "has a time component" in reasons[3]
    assert reasons[4] == "expected a date as YYYY-MM-DD, got '01/02/2024'"
    assert reasons[5] == "expected a date as YYYY-MM-DD, got 'Jan 2 2024'"


def test_xlsx_cells(client, dataset_id):
    content = _xlsx([
        ["midnight", dt.datetime(2024, 2, 29), dt.datetime(2024, 2, 29, 10, 30)],
        ["with time", dt.datetime(2024, 1, 2, 10, 30), None],
        ["a time", dt.time(10, 30), None],
        ["a duration", dt.timedelta(hours=2), None],
        ["serial", 45000, None],
        ["text cell", "2024-03-01", None],
    ])
    result = _commit(client, dataset_id, "d.xlsx", content)
    rows = _rows(dataset_id)
    # A datetime cell in a non-date column keeps its old str() form.
    assert rows == [
        {"name": "midnight", "as_of": "2024-02-29", "note": "2024-02-29 10:30:00"},
        {"name": "text cell", "as_of": "2024-03-01"},
    ]
    reasons = _reasons(result)
    assert reasons[2] == "2024-01-02 10:30:00 has a time component"
    assert reasons[3] == "a time is not a date"
    assert reasons[4] == "a time is not a date"
    assert reasons[5] == "expected a date as YYYY-MM-DD, got '45000'"


def test_xlsx_1904_date_system(client, dataset_id):
    content = _xlsx([["mac", dt.datetime(2024, 2, 29), None]], epoch=CALENDAR_MAC_1904)
    workbook = openpyxl.load_workbook(io.BytesIO(content))
    assert workbook.epoch == CALENDAR_MAC_1904
    _commit(client, dataset_id, "d.xlsx", content)
    assert _rows(dataset_id) == [{"name": "mac", "as_of": "2024-02-29"}]


def test_json_and_yaml_values(client, dataset_id):
    content = json.dumps([
        {"name": "ok", "as_of": "2024-02-29"},
        {"name": "number", "as_of": 20240229},
        {"name": "datetime", "as_of": "2024-02-29T00:00:00Z"},
    ]).encode()
    result = _commit(client, dataset_id, "d.json", content)
    assert result["imported"] == 1
    reasons = _reasons(result)
    assert reasons[2] == "20240229 is a number, not a date (write YYYY-MM-DD)"
    assert "has a time component" in reasons[3]

    # Unquoted YAML dates stay text (the shared reader never builds date objects).
    content = b"- {name: plain, as_of: 2024-03-01}\n- {name: plain dt, as_of: 2024-03-01 03:04:05}\n"
    result = _commit(client, dataset_id, "d.yaml", content)
    assert result["imported"] == 1
    assert "has a time component" in _reasons(result)[2]
    assert {"name": "plain", "as_of": "2024-03-01"} in _rows(dataset_id)


def test_inline_fixes_use_the_same_rules(client, dataset_id):
    content = b"name,as_of\none,01/02/2024\ntwo,01/02/2024\nthree,01/02/2024\n"
    result = _commit(
        client, dataset_id, "d.csv", content,
        fixes={"1": {"as_of": "2024-01-02"}, "2": {"as_of": "2024-01-02 09:00"}, "3": {"as_of": " 2024-01-03 "}},
    )
    assert result["imported"] == 2
    assert "has a time component" in _reasons(result)[2]
    assert [r["as_of"] for r in _rows(dataset_id)] == ["2024-01-02", "2024-01-03"]


def test_a_fix_replaces_a_coercion_failure(client, dataset_id):
    content = json.dumps([{"name": "x", "as_of": 20240229}]).encode()
    result = _commit(client, dataset_id, "d.json", content, fixes={"1": {"as_of": "2024-02-29"}})
    assert result == {"imported": 1, "skipped": 0, "errors": []}


def test_csv_and_xlsx_with_the_same_dates_give_identical_rows(client, as_role, dataset_id):
    csv_content = b"name,as_of\na,2024-02-29\nb,2025-12-31\n"
    xlsx_content = _xlsx([["a", dt.datetime(2024, 2, 29), None], ["b", dt.date(2025, 12, 31), None]])
    _commit(client, dataset_id, "d.csv", csv_content)
    from_csv = _rows(dataset_id)

    as_role("editor")
    other = client.post("/v1/datasets", json={"name": f"gl359-{uuid.uuid4().hex[:8]}"}).json()["id"]
    try:
        client.put(f"/v1/datasets/{other}/schema", json={"columns": COLUMNS})
        _commit(client, other, "d.xlsx", xlsx_content)
        assert _rows(other) == from_csv
    finally:
        db = SessionLocal()
        db.delete(db.get(Dataset, uuid.UUID(other)))
        db.commit()
        db.close()
