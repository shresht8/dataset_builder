"""GL-134: a schema change that would break stored rows is refused (422 + report)."""

from __future__ import annotations

import uuid

import pytest
from groundline_api.db import SessionLocal
from groundline_api.models.row import DatasetRow

TEXT = {"key": "score", "label": "Score", "type": "text"}
NUMBER = {"key": "score", "label": "Score", "type": "number"}
COLOUR = {"key": "colour", "label": "Colour", "type": "select", "options": ["red", "blue"]}
NAME = {"key": "name", "label": "Name", "type": "text"}


def _tag() -> str:
    return uuid.uuid4().hex[:8]


@pytest.fixture()
def make(client, as_role, cleanup):
    """make(columns, rows) -> dataset id, logged in as editor."""

    def _make(columns, rows=(), **extra):
        as_role("editor")
        resp = client.post("/v1/datasets", json={"name": f"gl134-{_tag()}", **extra})
        assert resp.status_code == 201, resp.text
        ds_id = resp.json()["id"]
        cleanup["datasets"].append(ds_id)
        assert client.put(f"/v1/datasets/{ds_id}/schema", json={"columns": columns}).status_code == 200
        for data in rows:
            assert client.post(f"/v1/datasets/{ds_id}/rows", json={"data": data}).status_code == 201
        return ds_id

    return _make


def _put(client, ds, columns, dry_run=False):
    url = f"/v1/datasets/{ds}/schema" + ("?dry_run=true" if dry_run else "")
    return client.put(url, json={"columns": columns})


def _types(client, ds) -> dict[str, str]:
    return {c["key"]: c["type"] for c in client.get(f"/v1/datasets/{ds}/schema").json()["columns"]}


def _rows(client, ds):
    return client.get(f"/v1/datasets/{ds}/rows").json()


SCORES = [{"score": "10"}, {"score": "abc"}, {"score": "3.5"}]


# --- the examples from the card ---------------------------------------------------------


def test_text_to_number_counts_every_string_row(client, make):
    ds = make([TEXT], SCORES)
    resp = _put(client, ds, [NUMBER])
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert isinstance(detail, str)
    assert "'score'" in detail and "3 rows" in detail and "can't be converted in place" in detail
    assert "'abc'" in detail
    assert _types(client, ds) == {"score": "text"}  # nothing was written


def test_required_added_while_rows_lack_a_value(client, make):
    ds = make([NAME, COLOUR], [{"name": "a", "colour": "red"}, {"name": "b"}])
    required = dict(COLOUR, required=True)
    resp = _put(client, ds, [NAME, required])
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "'colour'" in detail and "1 row" in detail and "required" in detail

    # Fixed rows: accepted.
    row = next(r for r in _rows(client, ds) if r["data"]["name"] == "b")
    resp = client.patch(
        f"/v1/datasets/{ds}/rows/{row['id']}",
        json={"data": {"name": "b", "colour": "blue"}},
        headers={"If-Match": str(row["rev"])},
    )
    assert resp.status_code == 200, resp.text
    assert _put(client, ds, [NAME, required]).status_code == 200


def test_select_option_removed_while_rows_use_it(client, make):
    ds = make([COLOUR], [{"colour": "red"}, {"colour": "blue"}])
    narrower = dict(COLOUR, options=["blue"])
    resp = _put(client, ds, [narrower])
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "'colour'" in detail and "1 row" in detail and "Fix these rows first" in detail
    # After the fix: accepted.
    row = next(r for r in _rows(client, ds) if r["data"]["colour"] == "red")
    client.patch(
        f"/v1/datasets/{ds}/rows/{row['id']}",
        json={"data": {"colour": "blue"}},
        headers={"If-Match": str(row["rev"])},
    )
    assert _put(client, ds, [narrower]).status_code == 200


def test_readding_an_archived_key_checks_its_old_values(client, make):
    ds = make([NAME, TEXT], [{"name": "a", "score": "abc"}, {"name": "b", "score": "10"}])
    assert _put(client, ds, [NAME]).status_code == 200  # archives score, values stay
    resp = _put(client, ds, [NAME, NUMBER])
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "'score'" in detail and "archived" in detail and "different key" in detail
    assert "2 rows" in detail
    # The same type comes back fine, and so does another key.
    assert _put(client, ds, [NAME, TEXT]).status_code == 200
    assert _put(client, ds, [NAME, dict(NUMBER, key="score2")]).status_code == 200


# --- what still passes -------------------------------------------------------------------------


def test_harmless_changes_pass(client, make):
    ds = make([TEXT, NAME], SCORES)
    json_col = dict(TEXT, type="json")
    assert _put(client, ds, [json_col, NAME]).status_code == 200  # text -> json accepts strings
    assert _put(client, ds, [dict(json_col, label="Points"), NAME]).status_code == 200  # label only
    ds2 = make([NAME], [{"name": "a"}])
    assert _put(client, ds2, [NAME, COLOUR]).status_code == 200  # new optional column
    assert _put(client, ds2, [NAME, dict(COLOUR, required=True)]).status_code == 422  # rows lack it


def test_legacy_broken_rows_do_not_block_unrelated_edits(client, make):
    ds = make([NAME, dict(NAME, key="age", type="number")], [{"name": "ok", "age": 1}])
    db = SessionLocal()
    try:  # a row that is already invalid, written straight into the table
        db.add(DatasetRow(dataset_id=uuid.UUID(ds), data={"name": "old", "age": "x"}))
        db.commit()
    finally:
        db.close()
    resp = _put(client, ds, [NAME, dict(NAME, key="age", type="number"), COLOUR])
    assert resp.status_code == 200, resp.text


def test_soft_deleted_rows_are_ignored(client, make):
    ds = make([TEXT], [{"score": "abc"}])
    row = _rows(client, ds)[0]
    resp = client.delete(f"/v1/datasets/{ds}/rows/{row['id']}", headers={"If-Match": str(row["rev"])})
    assert resp.status_code == 204, resp.text
    assert _put(client, ds, [NUMBER]).status_code == 200


def test_key_column_message_is_left_to_the_key_check(client, make):
    ds = make([NAME, dict(NAME, key="alt")], [{"name": "a", "alt": "x"}, {"name": "b"}])
    key = {"key": "alt", "label": "Alt", "type": "text", "required": True, "is_key": True}
    resp = _put(client, ds, [NAME, key])
    assert resp.status_code == 422, resp.text
    assert "can't make 'alt' the key column" in resp.json()["detail"]


# --- dry run, roles, store ------------------------------------------------------------------------


def test_dry_run_reports_and_writes_nothing(client, make):
    ds = make([TEXT], SCORES)
    resp = _put(client, ds, [NUMBER], dry_run=True)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False and "3 rows" in body["detail"]
    assert body["problems"][0]["column"] == "score" and body["problems"][0]["rows"] == 3
    assert _types(client, ds) == {"score": "text"}

    resp = _put(client, ds, [dict(TEXT, label="Points")], dry_run=True)
    assert resp.status_code == 200 and resp.json()["ok"] is True
    cols = client.get(f"/v1/datasets/{ds}/schema").json()["columns"]
    assert cols[0]["label"] == "Score"  # still the old label


def test_annotator_cannot_change_the_schema(client, as_role, make):
    ds = make([TEXT])
    as_role("annotator")
    assert _put(client, ds, [NUMBER]).status_code == 403
    assert _put(client, ds, [NUMBER], dry_run=True).status_code == 403


def _entries(client, cid):
    return client.get(f"/v1/collections/{cid}/schemas").json()


def test_rejected_put_and_apply_leave_no_store_entry(client, as_role, cleanup):
    as_role("editor")
    c = client.post("/v1/collections", json={"name": f"s-{_tag()}"}).json()
    cleanup["collections"].append(c["id"])
    ds = client.post(
        "/v1/datasets", json={"name": "d", "collection_id": c["id"], "columns": [TEXT]}
    ).json()
    for data in SCORES:
        client.post(f"/v1/datasets/{ds['id']}/rows", json={"data": data})
    before = len(_entries(client, c["id"]))

    assert _put(client, ds["id"], [NUMBER]).status_code == 422
    assert len(_entries(client, c["id"])) == before

    # Apply: another entry in the store whose `score` is a number.
    other = client.post(
        f"/v1/collections/{c['id']}/schemas", json={"name": "numeric", "columns": [NUMBER]}
    ).json()
    before = len(_entries(client, c["id"]))
    resp = client.post(f"/v1/datasets/{ds['id']}/schema/apply", json={"store_schema_id": other["id"]})
    assert resp.status_code == 422, resp.text
    assert "'score'" in resp.json()["detail"]
    assert len(_entries(client, c["id"])) == before
    dry = client.post(
        f"/v1/datasets/{ds['id']}/schema/apply?dry_run=true", json={"store_schema_id": other["id"]}
    )
    assert dry.status_code == 200 and dry.json()["ok"] is False
    assert _types(client, ds["id"]) == {"score": "text"}
