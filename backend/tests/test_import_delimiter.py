"""GL-141: CSV delimiter detection and override on import preview, commit and infer."""

from __future__ import annotations

import uuid

import pytest

from groundline_api.db import SessionLocal
from groundline_api.models.dataset import Dataset
from groundline_api.services.importer import detect_delimiter

COLUMNS = [
    {"key": "id", "label": "ID", "type": "text", "required": True, "is_key": True},
    {"key": "question", "label": "Question", "type": "long_text"},
    {"key": "score", "label": "Score", "type": "number"},
]


@pytest.fixture()
def dataset_id(client, as_role):
    as_role("editor")
    resp = client.post("/v1/datasets", json={"name": f"gl141-{uuid.uuid4().hex[:8]}", "columns": COLUMNS})
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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("id,question,score\nq1,What?,1\nq2,Why?,2\n", ","),
        ("id;question;score\nq1;What, then?;1\nq2;Why;2\n", ";"),
        ("id\tquestion\tscore\nq1\tWhat, then?\t1\n", "\t"),
        ("id|question|score\nq1|a;b|1\n", "|"),
        # Delimiters inside quoted and plain text cells don't win over the real one.
        ('id,question,score\nq1,"a;b;c;d",1\nq2,x;y,2\n', ","),
        # A multi-line quoted cell and uneven rows (like 08_dirty.csv).
        ('id,question,score\nq1,"Multi\nline; text",1\n\nq2,short\nq3,too,many,cells\nq4,ok,4\n', ","),
        # One column: nothing splits the header, so the fallback.
        ("question\nWhat; then?\nWhy, now?\n", ","),
        ("", ","),
    ],
)
def test_detect_delimiter(text, expected):
    assert detect_delimiter(text) == expected


def _preview(client, ds_id, content: bytes, **form):
    return client.post(
        f"/v1/datasets/{ds_id}/import/preview", files={"file": ("data.csv", content)}, data=form
    )


SEMICOLON = b"id;question;score\nq1;What, then?;1\nq2;Why;2.5\n"


def test_preview_and_commit_detect_semicolon(client, dataset_id):
    resp = _preview(client, dataset_id, SEMICOLON)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["delimiter"] == ";"
    assert body["columns"] == ["id", "question", "score"]
    assert body["sample_rows"][0]["values"]["question"] == "What, then?"

    mapping = '{"id": "id", "question": "question", "score": "score"}'
    resp = client.post(
        f"/v1/datasets/{dataset_id}/import",
        files={"file": ("data.csv", SEMICOLON)},
        data={"mapping": mapping},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["imported"] == 2
    rows = client.get(f"/v1/datasets/{dataset_id}/rows").json()
    assert sorted((r["data"]["id"], r["data"]["question"], r["data"]["score"]) for r in rows) == [
        ("q1", "What, then?", 1),
        ("q2", "Why", 2.5),
    ]


def test_given_delimiter_overrides_detection(client, dataset_id):
    resp = _preview(client, dataset_id, SEMICOLON, delimiter=",")
    assert resp.status_code == 200, resp.text
    assert resp.json()["delimiter"] == ","
    assert resp.json()["columns"] == ["id;question;score"]

    resp = _preview(client, dataset_id, b"id\tquestion\nq1\tx\n", delimiter="\t")
    assert resp.json()["columns"] == ["id", "question"]


def test_bad_delimiter_is_refused(client, dataset_id):
    resp = _preview(client, dataset_id, SEMICOLON, delimiter=":")
    assert resp.status_code == 422
    assert "delimiter" in resp.json()["detail"]


def test_non_csv_has_no_delimiter(client, dataset_id):
    resp = client.post(
        f"/v1/datasets/{dataset_id}/import/preview",
        files={"file": ("data.jsonl", b'{"id": "q1", "question": "x"}\n')},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["delimiter"] is None


def test_infer_detects_and_takes_a_delimiter(client, as_role):
    as_role("editor")
    resp = client.post("/v1/schema/infer", files={"file": ("data.csv", SEMICOLON)})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["delimiter"] == ";"
    assert [c["key"] for c in body["columns"]] == ["id", "question", "score"]

    resp = client.post("/v1/schema/infer", files={"file": ("data.csv", SEMICOLON)}, data={"delimiter": ","})
    assert [c["key"] for c in resp.json()["columns"]] == ["id;question;score"]

    resp = client.post("/v1/schema/infer", files={"file": ("data.json", b'[{"id": "a"}]')})
    assert resp.json()["delimiter"] is None
