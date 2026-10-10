"""GL-3.5-15: key-based row sync (upsert with conflict detection)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
import yaml
from groundline_api.db import SessionLocal
from groundline_api.models.dataset import Dataset
from groundline_api.models.row import DatasetRow, RowEdit
from groundline_schema.paths import nest, row_hash
from sqlalchemy import func, select

FIXTURE = Path(__file__).parent / "fixtures" / "eval_suite.yaml"

COLUMNS = [
    {"key": "id", "label": "ID", "type": "text", "required": True, "is_key": True},
    {"key": "question", "label": "Question", "type": "text", "required": True},
    {"key": "expected.answer", "label": "Answer", "type": "long_text"},
    {"key": "expected.score", "label": "Score", "type": "number"},
    {"key": "meta", "label": "Meta", "type": "json"},
]
BASE_RECORDS = [
    {"id": "a", "question": "qa", "expected": {"answer": "A", "score": 1}},
    {"id": "b", "question": "qb", "expected": {"answer": "B"}},
]


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


def _dataset(client, as_role, datasets, columns=COLUMNS) -> str:
    as_role("editor")
    resp = client.post("/v1/datasets", json={"name": f"gl3515-{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 201, resp.text
    ds_id = resp.json()["id"]
    datasets.append(ds_id)
    resp = client.put(f"/v1/datasets/{ds_id}/schema", json={"columns": columns})
    assert resp.status_code == 200, resp.text
    return ds_id


@pytest.fixture()
def ds(client, as_role, datasets):
    return _dataset(client, as_role, datasets)


def _push(client, ds_id, records, *, filename="f.json", revs=None, bases=None, **flags):
    content = records if isinstance(records, bytes) else json.dumps(records).encode()
    data = {k: str(v).lower() for k, v in flags.items()}
    if revs is not None:
        data["revs"] = json.dumps(revs)
    if bases is not None:
        data["bases"] = json.dumps(bases)
    return client.post(
        f"/v1/datasets/{ds_id}/sync", files={"file": (filename, content)}, data=data
    )


def _pushed(client, ds_id, records, **kwargs) -> dict:
    resp = _push(client, ds_id, records, **kwargs)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _rows(ds_id) -> dict[str, DatasetRow]:
    db = SessionLocal()
    try:
        rows = db.scalars(select(DatasetRow).where(DatasetRow.dataset_id == uuid.UUID(ds_id)))
        return {row.row_key: row for row in rows}
    finally:
        db.close()


def _audit_count(ds_id) -> int:
    db = SessionLocal()
    try:
        return db.scalar(
            select(func.count()).select_from(RowEdit).join(DatasetRow)
            .where(DatasetRow.dataset_id == uuid.UUID(ds_id))
        )
    finally:
        db.close()


def _pull_state(client, ds_id) -> tuple[dict, dict, list[dict]]:
    """What `rows pull` would record: revs, bases and the nested records."""
    keys = {c["key"] for c in client.get(f"/v1/datasets/{ds_id}/schema").json()["columns"]}
    rows = client.get(f"/v1/datasets/{ds_id}/rows").json()
    revs = {r["data"]["id"]: r["rev"] for r in rows}
    bases = {r["data"]["id"]: row_hash(r["data"], keys) for r in rows}
    records = [nest({k: v for k, v in r["data"].items() if v is not None}) for r in rows]
    return revs, bases, records


def _edit_in_ui(client, as_role, ds_id, key, data, role="annotator"):
    row = next(r for r in client.get(f"/v1/datasets/{ds_id}/rows").json() if r["data"]["id"] == key)
    as_role(role)
    resp = client.patch(
        f"/v1/datasets/{ds_id}/rows/{row['id']}", json={"data": data},
        headers={"If-Match": str(row["rev"])},
    )
    assert resp.status_code == 200, resp.text
    as_role("editor")


def _with(records, key, **changes) -> list[dict]:
    out = json.loads(json.dumps(records))
    record = next(r for r in out if r["id"] == key)
    for path, value in changes.items():
        node = record
        *parents, last = path.split("__")
        for part in parents:
            node = node.setdefault(part, {})
        node[last] = value
    return out


def test_create_then_identical_push_is_all_unchanged(client, ds):
    result = _pushed(client, ds, BASE_RECORDS)
    assert (result["applied"], result["created"], result["updated"]) == (True, 2, 0)
    assert set(result["revs"]) == {"a", "b"}
    revs_before = {k: r.rev for k, r in _rows(ds).items()}
    audit_before = _audit_count(ds)

    again = _pushed(client, ds, BASE_RECORDS, revs=result["revs"], bases=result["bases"])
    assert (again["applied"], again["created"], again["updated"], again["unchanged"]) == (True, 0, 0, 2)
    assert {k: r.rev for k, r in _rows(ds).items()} == revs_before
    assert _audit_count(ds) == audit_before

    # Same without bases: no field differs, so still unchanged.
    third = _pushed(client, ds, BASE_RECORDS, revs=result["revs"])
    assert third["unchanged"] == 2 and third["updated"] == 0


def test_conflict_when_both_sides_edited_the_same_row(client, as_role, ds):
    _pushed(client, ds, BASE_RECORDS)
    revs, bases, records = _pull_state(client, ds)
    _edit_in_ui(client, as_role, ds, "a", {"expected.answer": "A (fixed in UI)"})

    edited = _with(_with(records, "a", expected__answer="A (file)"), "b", expected__answer="B2")
    result = _pushed(client, ds, edited, revs=revs, bases=bases)
    assert result["applied"] is False
    assert [c["key"] for c in result["conflicts"]] == ["a"]
    assert result["conflicts"][0]["fields"] == {
        "expected.answer": {"server": "A (fixed in UI)", "file": "A (file)"}
    }
    assert [c["key"] for c in result["changes"]] == ["b"]
    assert _rows(ds)["b"].data["expected.answer"] == "B"  # nothing written

    without_a = [r for r in edited if r["id"] != "a"]
    result = _pushed(client, ds, without_a, revs=revs, bases=bases)
    assert result["applied"] is True and result["updated"] == 1
    assert _rows(ds)["b"].data["expected.answer"] == "B2"


def test_row_edited_only_on_the_server_is_server_newer_not_a_conflict(client, as_role, ds):
    """Decision l: base hashes tell unedited rows apart."""
    _pushed(client, ds, BASE_RECORDS)
    revs, bases, records = _pull_state(client, ds)
    _edit_in_ui(client, as_role, ds, "a", {"expected.answer": "A (fixed in UI)"})

    edited = _with(records, "b", expected__answer="B2")
    result = _pushed(client, ds, edited, revs=revs, bases=bases)
    assert result["applied"] is True
    assert result["updated"] == 1 and result["server_newer"] == 1
    assert result["server_newer_keys"] == ["a"]
    assert "a" not in result["revs"] and "a" not in result["bases"]
    rows = _rows(ds)
    assert rows["a"].data["expected.answer"] == "A (fixed in UI)"  # not overwritten
    assert rows["b"].data["expected.answer"] == "B2"

    # Without bases (revs only, refreshed from the push), the stale copy of A conflicts.
    refreshed = {**revs, **result["revs"]}
    result = _pushed(client, ds, _with(edited, "b", expected__answer="B3"), revs=refreshed)
    assert result["applied"] is False
    assert [c["key"] for c in result["conflicts"]] == ["a"]


def test_no_rev_conflicts_and_force_applies(client, ds):
    _pushed(client, ds, BASE_RECORDS)
    changed = _with(BASE_RECORDS, "a", question="qa2")
    result = _pushed(client, ds, changed)
    assert result["applied"] is False
    assert result["conflicts"][0]["reason"].startswith("exists in Groundline")

    result = _pushed(client, ds, changed, force=True)
    assert result["applied"] is True and result["updated"] == 1
    assert _rows(ds)["a"].data["question"] == "qa2"


def test_merge_semantics_absent_null_and_status(client, as_role, ds):
    _pushed(client, ds, BASE_RECORDS)
    rows = client.get(f"/v1/datasets/{ds}/rows").json()
    for row in rows:  # approve both
        resp = client.patch(f"/v1/datasets/{ds}/rows/{row['id']}", json={"status": "approved"},
                            headers={"If-Match": str(row["rev"])})
        assert resp.status_code == 200, resp.text
    revs, bases, _ = _pull_state(client, ds)

    records = [
        {"id": "a", "expected": {"score": None}},           # absent question/answer kept; null clears
        {"id": "b", "question": "qb", "meta": None},         # null where there's no value: no change
    ]
    result = _pushed(client, ds, records, revs=revs, bases=bases)
    assert result["applied"] is True, result
    assert (result["updated"], result["unchanged"]) == (1, 1)
    rows = _rows(ds)
    assert rows["a"].data == {"id": "a", "question": "qa", "expected.answer": "A"}
    assert rows["a"].status.value == "needs_review"
    assert rows["b"].status.value == "approved"


def test_object_valued_json_column_round_trips(client, as_role, datasets):
    ds_id = _dataset(client, as_role, datasets)
    records = [{"id": "a", "question": "q", "meta": {"source": {"file": "x.yaml"}, "n": 1}}]
    _pushed(client, ds_id, records)
    revs, bases, pulled = _pull_state(client, ds_id)
    assert pulled[0]["meta"] == {"source": {"file": "x.yaml"}, "n": 1}

    as_yaml = yaml.safe_dump({"records": pulled}).encode()
    result = _pushed(client, ds_id, as_yaml, filename="f.yaml", revs=revs, bases=bases)
    assert result["errors"] == []
    assert result["unchanged"] == 1


def test_request_errors(client, as_role, datasets, ds):
    result = _pushed(client, ds, [{"id": "a", "question": "q", "expected": {"has_txt": True}}])
    assert result["applied"] is False
    assert result["errors"][0]["column"] == "expected.has_txt"

    result = _pushed(
        client, ds, [{"id": "a", "question": "q", "expected": {"has_txt": True}}],
        ignore_unknown=True,
    )
    assert result["applied"] is True
    assert result["ignored_paths"] == ["expected.has_txt"]

    result = _pushed(client, ds, [{"id": "x", "question": "q"}, {"id": "x", "question": "r"}])
    assert result["applied"] is False
    assert "duplicate key in file" in result["errors"][0]["reason"]

    result = _pushed(client, ds, [{"question": "no key"}])
    assert result["errors"][0]["reason"] == "missing key value"

    keyless = _dataset(client, as_role, datasets, [{"key": "q", "label": "Q", "type": "text"}])
    resp = _push(client, keyless, [{"q": "x"}])
    assert resp.status_code == 422
    assert "no key column" in resp.json()["detail"]

    resp = _push(client, ds, b"id,question\na,q\n", filename="f.csv")
    assert resp.status_code == 422

    as_role("annotator")
    assert _push(client, ds, BASE_RECORDS).status_code == 403
    as_role("editor")


def test_dry_run_writes_nothing(client, ds):
    result = _pushed(client, ds, BASE_RECORDS, dry_run=True)
    assert result["applied"] is False and result["created"] == 2
    assert result["revs"] == {}
    assert _rows(ds) == {}


def test_fixture_sync_round_trip(client, as_role, datasets):
    cases = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))["cases"]
    columns = [
        {"key": "id", "label": "ID", "type": "text", "required": True, "is_key": True},
        {"key": "prompt", "label": "Prompt", "type": "text"},
        {"key": "title", "label": "Title", "type": "text"},
        {"key": "topic", "label": "Topic", "type": "text"},
        {"key": "step", "label": "Step", "type": "number"},
        {"key": "thread_id", "label": "Thread", "type": "text"},
        {"key": "expected.routes", "label": "Routes", "type": "json"},
        {"key": "expected.tools", "label": "Tools", "type": "json"},
        {"key": "expected.calls", "label": "Calls", "type": "json"},
        {"key": "expected.answer", "label": "Answer", "type": "long_text"},
        {"key": "expected.has_text", "label": "Has text", "type": "boolean"},
        {"key": "expected.has_table", "label": "Has table", "type": "boolean"},
        {"key": "expected.match_mode", "label": "Match", "type": "text"},
        {"key": "expected.follow_up", "label": "Follow-up", "type": "boolean"},
    ]
    ds_id = _dataset(client, as_role, datasets, columns)
    content = FIXTURE.read_bytes()

    first = _pushed(client, ds_id, content, filename="suite.yaml")
    assert first["applied"] and first["created"] == len(cases)

    again = _pushed(client, ds_id, content, filename="suite.yaml",
                    revs=first["revs"], bases=first["bases"])
    assert (again["updated"], again["unchanged"]) == (0, len(cases))  # null fields: no change

    edited = content.replace(
        b"Placeholder answer listing late orders and contacts.",
        b"Late orders: none this week.",
    )
    result = _pushed(client, ds_id, edited, filename="suite.yaml",
                     revs=first["revs"], bases=first["bases"])
    assert (result["updated"], result["unchanged"]) == (1, len(cases) - 1)
    assert result["changes"] == [{
        "key": "case-orders-01", "op": "update",
        "fields": {"expected.answer": {
            "before": "Placeholder answer listing late orders and contacts.\n",
            "after": "Late orders: none this week.\n",
        }},
    }]
