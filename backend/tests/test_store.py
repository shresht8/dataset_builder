"""GL-115: schema store API and store-backed dataset schemas (Phase 4, D5–D9, C3)."""

from __future__ import annotations

import uuid

from groundline_schema import schema_hash

A_COLUMNS = [
    {"key": "id", "label": "Id", "type": "text", "required": True, "is_key": True},
    {"key": "question", "label": "Question", "type": "long_text"},
]
B_COLUMNS = A_COLUMNS + [{"key": "notes", "label": "Notes", "type": "text"}]


def _tag() -> str:
    return uuid.uuid4().hex[:8]


def _collection(client, cleanup) -> dict:
    resp = client.post("/v1/collections", json={"name": f"s-{_tag()}"})
    assert resp.status_code == 201, resp.text
    cleanup["collections"].append(resp.json()["id"])
    return resp.json()


def _create(client, cid, name, **extra):
    resp = client.post("/v1/datasets", json={"name": name, "collection_id": cid, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _entries(client, cid) -> dict[str, dict]:
    return {e["name"]: e for e in client.get(f"/v1/collections/{cid}/schemas").json()}


def _assert_in_step(client, cid, dataset_id):
    """C3 invariant: a collection dataset's active columns hash to its entry's content_hash."""
    schema = client.get(f"/v1/datasets/{dataset_id}/schema").json()
    entry = client.get(f"/v1/collections/{cid}/schemas/{schema['store_schema']['id']}").json()
    assert schema_hash(schema["columns"]) == entry["content_hash"]


def test_store_crud_duplicate_and_freeze(client, as_role, cleanup):
    as_role("editor")
    c = _collection(client, cleanup)
    base = f"/v1/collections/{c['id']}/schemas"

    resp = client.post(base, json={"name": "qa", "columns": A_COLUMNS})
    assert resp.status_code == 201, resp.text
    qa = resp.json()
    assert qa["used_by"] == [] and qa["content_hash"] == schema_hash(A_COLUMNS)
    assert client.post(base, json={"name": "qa", "columns": A_COLUMNS}).status_code == 409
    bad = [{"key": "x", "label": "X", "type": "text", "options": ["a"]}]
    resp = client.post(base, json={"name": "bad", "columns": bad})
    assert resp.status_code == 422 and "options are only allowed" in resp.json()["detail"]

    # Unused: edit and rename freely.
    resp = client.put(f"{base}/{qa['id']}", json={"columns": B_COLUMNS})
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["columns"]) == 3
    assert client.put(f"{base}/{qa['id']}", json={"columns": A_COLUMNS}).status_code == 200

    # Duplicate, then use the original.
    resp = client.post(f"{base}/{qa['id']}/duplicate", json={"name": "qa-copy"})
    assert resp.status_code == 201, resp.text
    copy = resp.json()
    assert copy["content_hash"] == qa["content_hash"]
    ds = _create(client, c["id"], "train", store_schema_id=qa["id"])
    assert ds["store_schema"] == {"id": qa["id"], "name": "qa", "created": False}

    # In use: PUT and DELETE are 409 with used_by.
    for resp in (
        client.put(f"{base}/{qa['id']}", json={"columns": B_COLUMNS}),
        client.delete(f"{base}/{qa['id']}"),
    ):
        assert resp.status_code == 409
        assert [u["path"] for u in resp.json()["used_by"]] == [f"{c['name']}/train"]
        assert "duplicate it to change it" in resp.json()["detail"]
    assert _entries(client, c["id"])["qa"]["used_by"][0]["id"] == ds["id"]

    # The unused copy can be deleted.
    assert client.delete(f"{base}/{copy['id']}").status_code == 204
    assert "qa-copy" not in _entries(client, c["id"])


def test_d7_resolve_or_add(client, as_role, cleanup):
    as_role("editor")
    c = _collection(client, cleanup)

    a = _create(client, c["id"], "a", columns=A_COLUMNS)
    assert a["store_schema"]["name"] == "a" and a["store_schema"]["created"] is True

    b = _create(client, c["id"], "b", columns=A_COLUMNS)  # identical: reused
    assert b["store_schema"] == {**a["store_schema"], "created": False}

    relabelled = [dict(A_COLUMNS[0]), {**A_COLUMNS[1], "label": "Prompt"}]
    c3 = _create(client, c["id"], "c3", columns=relabelled)  # one label differs: new entry
    assert c3["store_schema"]["name"] == "c3" and c3["store_schema"]["created"] is True

    # A clash on the preferred name gets a suffix: PUT on `a` with new columns
    # wants entry `a`, which exists.
    resp = client.put(f"/v1/datasets/{a['id']}/schema", json={"columns": B_COLUMNS})
    assert resp.status_code == 200, resp.text
    assert resp.json()["store_schema"]["name"] == "a-2"
    assert resp.json()["store_schema"]["created"] is True
    assert set(_entries(client, c["id"])) == {"a", "a-2", "c3"}

    # Exactly one of store_schema_id / columns in a collection.
    resp = client.post("/v1/datasets", json={"name": "z", "collection_id": c["id"]})
    assert resp.status_code == 422
    for ds in (a, b, c3):
        _assert_in_step(client, c["id"], ds["id"])


def test_d8_apply_archives_and_refuses_bad_keys(client, as_role, cleanup):
    as_role("editor")
    c = _collection(client, cleanup)
    base = f"/v1/collections/{c['id']}/schemas"
    ds = _create(client, c["id"], "train", columns=B_COLUMNS)
    for key, note in (("q1", "n1"), ("q2", "n2")):
        resp = client.post(
            f"/v1/datasets/{ds['id']}/rows",
            json={"data": {"id": key, "question": "same", "notes": note}},
        )
        assert resp.status_code == 201, resp.text

    # Apply an entry without `notes`: archived, old rows keep their data.
    slim = client.post(base, json={"name": "slim", "columns": A_COLUMNS}).json()
    resp = client.post(f"/v1/datasets/{ds['id']}/schema/apply", json={"store_schema_id": slim["id"]})
    assert resp.status_code == 200, resp.text
    assert [col["key"] for col in resp.json()["columns"]] == ["id", "question"]
    archived = client.get(f"/v1/datasets/{ds['id']}/schema?include_archived=true").json()["columns"]
    assert {col["key"]: col["archived"] for col in archived}["notes"] is True
    rows = client.get(f"/v1/datasets/{ds['id']}/rows").json()
    assert {r["data"]["notes"] for r in rows} == {"n1", "n2"}
    _assert_in_step(client, c["id"], ds["id"])

    # An entry keyed on `question` (duplicated values): 422, nothing changes.
    by_question = [
        {"key": "id", "label": "Id", "type": "text", "required": True},
        {"key": "question", "label": "Question", "type": "text", "required": True, "is_key": True},
    ]
    dupe = client.post(base, json={"name": "dupe", "columns": by_question}).json()
    resp = client.post(f"/v1/datasets/{ds['id']}/schema/apply", json={"store_schema_id": dupe["id"]})
    assert resp.status_code == 422 and "can't make 'question' the key column" in resp.json()["detail"]
    schema = client.get(f"/v1/datasets/{ds['id']}/schema").json()
    assert schema["store_schema"]["name"] == "slim"
    assert [col["key"] for col in schema["columns"] if col["is_key"]] == ["id"]

    # An entry from another collection: 422.
    other = _collection(client, cleanup)
    foreign = client.post(
        f"/v1/collections/{other['id']}/schemas", json={"name": "f", "columns": A_COLUMNS}
    ).json()
    resp = client.post(f"/v1/datasets/{ds['id']}/schema/apply", json={"store_schema_id": foreign["id"]})
    assert resp.status_code == 422


def test_move_between_collections_and_unfiled(client, as_role, cleanup):
    as_role("editor")
    tag = _tag()
    resp = client.post("/v1/datasets", json={"name": f"m-{tag}", "columns": A_COLUMNS})
    assert resp.status_code == 201, resp.text
    ds = resp.json()
    cleanup["datasets"].append(ds["id"])
    assert ds["store_schema"] is None and client.get(f"/v1/datasets/{ds['id']}/schema").json()["store_schema"] is None

    c1, c2 = _collection(client, cleanup), _collection(client, cleanup)
    f = client.post(f"/v1/collections/{c2['id']}/folders", json={"name": "f"}).json()
    existing = client.post(
        f"/v1/collections/{c2['id']}/schemas", json={"name": "same", "columns": A_COLUMNS}
    ).json()

    # Unfiled -> c1: the schema is added to c1's store.
    resp = client.post(f"/v1/datasets/{ds['id']}/move", json={"collection_id": c1["id"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["path"] == f"{c1['name']}/m-{tag}"
    assert resp.json()["store_schema"]["created"] is True
    _assert_in_step(client, c1["id"], ds["id"])

    # c1 -> c2/f: an identical entry exists there and is reused.
    resp = client.post(f"/v1/datasets/{ds['id']}/move", json={"collection_id": c2["id"], "folder_id": f["id"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["store_schema"] == {"id": existing["id"], "name": "same", "created": False}
    assert resp.json()["path"] == f"{c2['name']}/f/m-{tag}"

    # Within c2 (to the root): keeps its entry.
    resp = client.post(f"/v1/datasets/{ds['id']}/move", json={"collection_id": c2["id"]})
    assert resp.json()["store_schema"]["id"] == existing["id"]

    # Name clash in the target: 409 and nothing moves.
    clash = _create(client, c1["id"], f"m-{tag}", columns=A_COLUMNS)
    resp = client.post(f"/v1/datasets/{ds['id']}/move", json={"collection_id": c1["id"]})
    assert resp.status_code == 409
    assert client.get(f"/v1/datasets/{ds['id']}").json()["collection_id"] == c2["id"]
    assert clash["id"] != ds["id"]

    # Back to unfiled: store entry cleared, columns unchanged.
    resp = client.post(f"/v1/datasets/{ds['id']}/move", json={"collection_id": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["collection_id"] is None and resp.json()["store_schema"] is None
    assert resp.json()["path"] == f"m-{tag}"
    keys = [col["key"] for col in client.get(f"/v1/datasets/{ds['id']}/schema").json()["columns"]]
    assert keys == ["id", "question"]


def test_every_write_route_keeps_columns_equal_to_the_entry(client, as_role, cleanup):
    as_role("editor")
    c = _collection(client, cleanup)
    entry = client.post(
        f"/v1/collections/{c['id']}/schemas", json={"name": "e", "columns": B_COLUMNS}
    ).json()
    with_columns = _create(client, c["id"], "w1", columns=A_COLUMNS)
    _assert_in_step(client, c["id"], with_columns["id"])
    with_entry = _create(client, c["id"], "w2", store_schema_id=entry["id"])
    _assert_in_step(client, c["id"], with_entry["id"])
    client.put(f"/v1/datasets/{with_entry['id']}/schema", json={"columns": A_COLUMNS})
    _assert_in_step(client, c["id"], with_entry["id"])
    client.post(f"/v1/datasets/{with_columns['id']}/schema/apply", json={"store_schema_id": entry["id"]})
    _assert_in_step(client, c["id"], with_columns["id"])
    other = _collection(client, cleanup)
    client.post(f"/v1/datasets/{with_columns['id']}/move", json={"collection_id": other["id"]})
    _assert_in_step(client, other["id"], with_columns["id"])


def test_store_rbac(client, as_role, cleanup):
    as_role("editor")
    c = _collection(client, cleanup)
    base = f"/v1/collections/{c['id']}/schemas"
    entry = client.post(base, json={"name": "e", "columns": A_COLUMNS}).json()
    ds = _create(client, c["id"], "d", store_schema_id=entry["id"])

    as_role("annotator")
    for method, url, body in [
        ("post", base, {"name": "x", "columns": A_COLUMNS}),
        ("put", f"{base}/{entry['id']}", {"name": "y"}),
        ("delete", f"{base}/{entry['id']}", None),
        ("post", f"{base}/{entry['id']}/duplicate", {"name": "z"}),
        ("post", "/v1/datasets", {"name": "n", "collection_id": c["id"], "store_schema_id": entry["id"]}),
        ("post", f"/v1/datasets/{ds['id']}/move", {"collection_id": None}),
        ("post", f"/v1/datasets/{ds['id']}/schema/apply", {"store_schema_id": entry["id"]}),
        ("put", f"/v1/datasets/{ds['id']}/schema", {"columns": A_COLUMNS}),
    ]:
        kwargs = {"json": body} if body is not None else {}
        assert getattr(client, method)(url, **kwargs).status_code == 403, (method, url)

    as_role("viewer")
    assert client.get(base).status_code == 200
    assert client.get(f"{base}/{entry['id']}").status_code == 200
