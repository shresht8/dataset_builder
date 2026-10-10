"""GL-3.5-7: the `date` column type — shared parsing and the schema round trip.

shared/ has no test suite of its own; its tests live here (see Makefile).
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from groundline_api.db import SessionLocal
from groundline_api.models.dataset import Dataset
from groundline_schema import ColumnType
from groundline_schema.temporal import format_date, is_date, parse_date


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2024-02-29", date(2024, 2, 29)),
        ("0001-01-01", date(1, 1, 1)),
        ("9999-12-31", date(9999, 12, 31)),
    ],
)
def test_parse_date_accepts_canonical_dates(text, expected):
    assert parse_date(text) == expected
    assert format_date(expected) == text


@pytest.mark.parametrize(
    "text",
    [
        "2023-02-29",           # not a real date
        "2024-02-30",
        "2024-13-01",
        "0000-01-01",           # year 0
        "10000-01-01",          # 5-digit year
        "2024-1-2",             # not zero-padded
        "20240102",             # basic ISO form (Python accepts it; we don't)
        "2024-01-02T00:00:00Z", # a datetime is not a date
        "2024-01-02 ",
        "",
        20240102,
        None,
    ],
)
def test_parse_date_rejects_everything_else(text):
    with pytest.raises(ValueError):
        parse_date(text)
    assert is_date(text) is False


def test_date_is_a_column_type():
    assert ColumnType("date") is ColumnType.DATE


@pytest.fixture()
def dataset_id(client, as_role):
    as_role("editor")
    resp = client.post("/v1/datasets", json={"name": f"gl357-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 201, resp.text
    ds_id = resp.json()["id"]
    yield ds_id
    db = SessionLocal()
    try:
        ds = db.get(Dataset, uuid.UUID(ds_id))
        if ds is not None:
            db.delete(ds)
            db.commit()
    finally:
        db.close()


def test_put_schema_round_trips_a_date_column(client, dataset_id):
    columns = [{"key": "as_of", "label": "As of", "type": "date", "required": True}]
    resp = client.put(f"/v1/datasets/{dataset_id}/schema", json={"columns": columns})
    assert resp.status_code == 200, resp.text
    got = client.get(f"/v1/datasets/{dataset_id}/schema").json()["columns"]
    assert got[0]["type"] == "date" and got[0]["required"] is True
