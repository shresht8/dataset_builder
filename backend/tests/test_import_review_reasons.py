"""GL-149: plain reason for an empty required value; one summary line per kind of problem."""

from __future__ import annotations

import json
import os
import uuid

import pytest
from groundline_api.db import SessionLocal
from groundline_api.models.dataset import Dataset
from groundline_api.services.importer import MISSING_VALUE
from groundline_schema.records import RecordsError, decode_text

COLUMNS = [
    {"key": "id", "label": "ID", "type": "text", "required": True, "is_key": True},
    {"key": "score", "label": "Score", "type": "number"},
]
MAPPING = json.dumps({"id": "id", "score": "score"})


@pytest.fixture()
def dataset_id(client, as_role):
    as_role("editor")
    resp = client.post(
        "/v1/datasets",
        json={"name": f"gl149-{uuid.uuid4().hex[:8]}", "columns": COLUMNS},
    )
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


def _validation(client, dataset_id, content):
    resp = client.post(
        f"/v1/datasets/{dataset_id}/import/preview",
        files={"file": ("data.csv", content)},
        data={"mapping": MAPPING},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["validation"]


def test_empty_required_value_says_what_to_do(client, dataset_id):
    validation = _validation(client, dataset_id, b"id,score\n,1\n")
    assert validation["errors"] == [{"row": 1, "column": "id", "reason": MISSING_VALUE}]
    assert "required" in MISSING_VALUE and "skip" in MISSING_VALUE


def test_summary_keeps_each_kind_of_problem_apart(client, dataset_id):
    # Row 2 repeats a key, row 3 has none: two problems in the same column.
    validation = _validation(client, dataset_id, b"id,score\na,1\na,2\n,3\n")
    assert validation["by_column"] == [
        {
            "column": "id",
            "rows": 1,
            "reason": '"a" is also the key of row 1. Keys must be unique, so skip one of the two rows',
        },
        {"column": "id", "rows": 1, "reason": MISSING_VALUE},
    ]


def test_summary_marks_one_row_value_as_an_example(client, dataset_id):
    validation = _validation(client, dataset_id, b"id,score\na,x\nb,y\nc,y\n")
    assert validation["by_column"] == [
        {"column": "score", "rows": 3, "reason": 'for example "x" is not a number'}
    ]


def test_same_reason_on_every_row_is_shown_as_is(client, dataset_id):
    validation = _validation(client, dataset_id, b"id,score\na,x\nb,x\n")
    assert validation["by_column"] == [
        {"column": "score", "rows": 2, "reason": '"x" is not a number'}
    ]


def test_binary_file_is_not_called_an_encoding_problem():
    with pytest.raises(RecordsError) as exc:
        decode_text(b"\x1d\x13\x00\xde\xd6#{" + os.urandom(64) + b"\x00", csv=True)
    assert "not UTF-8" not in str(exc.value)
    assert "not a text file" in str(exc.value)


def test_other_encoding_still_says_save_as_utf8():
    with pytest.raises(RecordsError) as exc:
        decode_text("name\ncafé – x\n".encode("cp1252"), csv=True)
    assert str(exc.value) == 'file is not UTF-8 — save it as "CSV UTF-8"'
    with pytest.raises(RecordsError) as exc:
        decode_text("﻿name\ncafé\n".encode("utf-16"), csv=True)
    assert "not UTF-8" in str(exc.value)
