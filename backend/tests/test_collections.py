"""GL-113 / GL-114: collections, folders, dataset paths, name uniqueness (Phase 4)."""

from __future__ import annotations

import uuid

import pytest
from groundline_api.db import SessionLocal
from groundline_api.models.collection import Collection, CollectionSchema, Folder
from groundline_api.models.dataset import Dataset
from sqlalchemy.exc import IntegrityError

COLUMNS = [{"key": "id", "label": "Id", "type": "text", "required": True, "is_key": True}]


def _tag() -> str:
    return uuid.uuid4().hex[:8]


def _collection(client, cleanup, name: str | None = None) -> dict:
    resp = client.post("/v1/collections", json={"name": name or f"c-{_tag()}", "description": "d"})
    assert resp.status_code == 201, resp.text
    cleanup["collections"].append(resp.json()["id"])
    return resp.json()


def _folder(client, cid: str, name: str, parent_id: str | None = None) -> dict:
    resp = client.post(f"/v1/collections/{cid}/folders", json={"name": name, "parent_id": parent_id})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _dataset(client, name: str, cid: str | None = None, fid: str | None = None):
    payload: dict = {"name": name, "columns": COLUMNS}
    if cid:
        payload.update(collection_id=cid, folder_id=fid)
    return client.post("/v1/datasets", json=payload)


# --- GL-113: database constraints -------------------------------------------------


def test_db_name_uniqueness_is_scoped_to_the_parent():
    tag = _tag()
    db = SessionLocal()
    try:
        collection = Collection(name=f"db-{tag}")
        db.add(collection)
        db.flush()
        entry = CollectionSchema(collection_id=collection.id, name="s", columns=[], content_hash="sha256:x")
        f1 = Folder(collection_id=collection.id, name="f1")
        f2 = Folder(collection_id=collection.id, name="f2")
        db.add_all([entry, f1, f2])
        db.flush()

        def ds(name, folder=None, coll=collection.id, store=entry.id):
            return Dataset(name=name, collection_id=coll, folder_id=folder, store_schema_id=store)

        # `train` in two folders, and at the root: fine.
        db.add_all([ds("train", f1.id), ds("train", f2.id), ds("train")])
        db.flush()

        for bad in (ds("train", f1.id), ds("train")):  # same folder; same root (NULLS NOT DISTINCT)
            nested = db.begin_nested()
            db.add(bad)
            with pytest.raises(IntegrityError):
                db.flush()
            nested.rollback()

        # Two unfiled `train-<tag>`: violation.
        db.add(Dataset(name=f"train-{tag}"))
        db.flush()
        nested = db.begin_nested()
        db.add(Dataset(name=f"train-{tag}"))
        with pytest.raises(IntegrityError):
            db.flush()
        nested.rollback()

        # In a collection without a store entry: check violation.
        nested = db.begin_nested()
        db.add(ds("other", store=None))
        with pytest.raises(IntegrityError, match="ck_datasets_store_schema_iff_collection"):
            db.flush()
        nested.rollback()

        # A folder without a collection: check violation.
        nested = db.begin_nested()
        db.add(Dataset(name=f"x-{tag}", folder_id=f1.id))
        with pytest.raises(IntegrityError, match="ck_datasets_folder_needs_collection"):
            db.flush()
        nested.rollback()
    finally:
        db.rollback()
        db.close()


# --- GL-114: collections + folders -------------------------------------------------


def test_collection_crud_and_tree(client, as_role, cleanup):
    as_role("editor")
    c = _collection(client, cleanup)
    assert c["dataset_count"] == 0

    resp = client.post("/v1/collections", json={"name": c["name"]})
    assert resp.status_code == 409
    assert resp.json()["detail"] == f"collection '{c['name']}' already exists"
    assert client.post("/v1/collections", json={"name": "Bad Name"}).status_code == 422

    f1 = _folder(client, c["id"], "f1")
    f2 = _folder(client, c["id"], "f2", f1["id"])
    f3 = _folder(client, c["id"], "f3", f2["id"])  # three deep
    assert _dataset(client, "train", c["id"], f3["id"]).status_code == 201

    names = [x["name"] for x in client.get("/v1/collections").json()]
    assert c["name"] in names

    tree = client.get(f"/v1/collections/{c['id']}").json()
    assert {f["name"] for f in tree["folders"]} == {"f1", "f2", "f3"}
    assert tree["dataset_count"] == 1
    (ds,) = tree["datasets"]
    assert ds["name"] == "train" and ds["folder_id"] == f3["id"]
    assert ds["store_schema_name"] == "train"
    assert ds["row_count"] == 0 and ds["version_count"] == 0

    new_name = f"c2-{_tag()}"
    resp = client.patch(f"/v1/collections/{c['id']}", json={"name": new_name, "description": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == new_name and resp.json()["description"] is None
    assert client.get(f"/v1/datasets/resolve?path={new_name}/f1/f2/f3/train").status_code == 200


def test_folder_rules(client, as_role, cleanup):
    as_role("editor")
    c = _collection(client, cleanup)
    other = _collection(client, cleanup)
    a = _folder(client, c["id"], "a")
    b = _folder(client, c["id"], "b", a["id"])
    _folder(client, c["id"], "b")  # same name under a different parent: fine

    resp = client.post(f"/v1/collections/{c['id']}/folders", json={"name": "a"})
    assert resp.status_code == 409
    assert resp.json()["detail"] == f"folder 'a' already exists in {c['name']}"
    resp = client.post(f"/v1/collections/{c['id']}/folders", json={"name": "b", "parent_id": a["id"]})
    assert resp.json()["detail"] == f"folder 'b' already exists in {c['name']}/a"

    # Re-parent into its own subtree: 422; into another collection's folder: 422.
    assert client.patch(f"/v1/folders/{a['id']}", json={"parent_id": b["id"]}).status_code == 422
    assert client.patch(f"/v1/folders/{a['id']}", json={"parent_id": a["id"]}).status_code == 422
    x = _folder(client, other["id"], "x")
    assert client.patch(f"/v1/folders/{b['id']}", json={"parent_id": x["id"]}).status_code == 422

    # Move b to the root: clashes with the root `b`.
    assert client.patch(f"/v1/folders/{b['id']}", json={"parent_id": None}).status_code == 409
    resp = client.patch(f"/v1/folders/{b['id']}", json={"name": "b2", "parent_id": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["parent_id"] is None and resp.json()["name"] == "b2"

    # Non-empty folder / collection deletes: 409.
    child = _folder(client, c["id"], "child", a["id"])
    assert client.delete(f"/v1/folders/{a['id']}").status_code == 409
    assert client.delete(f"/v1/folders/{child['id']}").status_code == 204
    assert _dataset(client, "ds", c["id"], a["id"]).status_code == 201
    assert client.delete(f"/v1/folders/{a['id']}").status_code == 409
    resp = client.delete(f"/v1/collections/{c['id']}")
    assert resp.status_code == 409 and "holds 1 dataset" in resp.json()["detail"]

    # An empty collection (with folders and store entries) can be deleted.
    assert client.delete(f"/v1/collections/{other['id']}").status_code == 204
    assert client.get(f"/v1/collections/{other['id']}").status_code == 404


def test_resolve_and_paths(client, as_role, cleanup):
    as_role("editor")
    tag = _tag()
    unfiled = _dataset(client, f"train-{tag}")
    assert unfiled.status_code == 201
    cleanup["datasets"].append(unfiled.json()["id"])
    assert unfiled.json()["path"] == f"train-{tag}"
    assert unfiled.json()["collection_id"] is None

    c = _collection(client, cleanup)
    f1 = _folder(client, c["id"], "f1")
    f2 = _folder(client, c["id"], "f2", f1["id"])
    root = _dataset(client, "train", c["id"])
    deep = _dataset(client, "train", c["id"], f2["id"])
    assert root.status_code == 201 and deep.status_code == 201
    assert root.json()["path"] == f"{c['name']}/train"
    assert deep.json()["path"] == f"{c['name']}/f1/f2/train"

    as_role("viewer")
    for path, expected in (
        (f"train-{tag}", unfiled.json()["id"]),
        (f"{c['name']}/train", root.json()["id"]),
        (f"{c['name']}/f1/f2/train", deep.json()["id"]),
    ):
        resp = client.get("/v1/datasets/resolve", params={"path": path})
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == expected and resp.json()["path"] == path

    resp = client.get("/v1/datasets/resolve", params={"path": f"{c['name']}/nope/train"})
    assert resp.status_code == 404 and "no folder 'nope'" in resp.json()["detail"]
    resp = client.get("/v1/datasets/resolve", params={"path": f"nope-{tag}/train"})
    assert resp.status_code == 404 and f"no collection 'nope-{tag}'" in resp.json()["detail"]
    resp = client.get("/v1/datasets/resolve", params={"path": f"{c['name']}/f1/missing"})
    assert resp.status_code == 404 and "no dataset 'missing'" in resp.json()["detail"]
    resp = client.get("/v1/datasets/resolve", params={"path": f"{c['name']}/Bad"})
    assert resp.status_code == 422 and "'Bad'" in resp.json()["detail"]

    listed = client.get("/v1/datasets", params={"collection_id": c["id"]}).json()
    assert {d["path"] for d in listed} == {f"{c['name']}/train", f"{c['name']}/f1/f2/train"}
    listed = client.get("/v1/datasets", params={"folder_id": f2["id"]}).json()
    assert [d["id"] for d in listed] == [deep.json()["id"]]
    unfiled_ids = {d["id"] for d in client.get("/v1/datasets", params={"unfiled": "true"}).json()}
    assert unfiled.json()["id"] in unfiled_ids and root.json()["id"] not in unfiled_ids


def test_dataset_name_clash_in_a_collection(client, as_role, cleanup):
    as_role("editor")
    c = _collection(client, cleanup)
    f = _folder(client, c["id"], "f")
    assert _dataset(client, "train", c["id"], f["id"]).status_code == 201
    resp = _dataset(client, "train", c["id"], f["id"])
    assert resp.status_code == 409
    assert resp.json()["detail"] == f"dataset 'train' already exists in {c['name']}/f"
    # Folder from another collection: 422.
    other = _collection(client, cleanup)
    resp = _dataset(client, "x", other["id"], f["id"])
    assert resp.status_code == 422


def test_rbac(client, as_role, cleanup):
    as_role("editor")
    c = _collection(client, cleanup)
    f = _folder(client, c["id"], "f")

    as_role("annotator")
    writes = [
        ("post", "/v1/collections", {"name": f"x-{_tag()}"}),
        ("patch", f"/v1/collections/{c['id']}", {"description": "x"}),
        ("delete", f"/v1/collections/{c['id']}", None),
        ("post", f"/v1/collections/{c['id']}/folders", {"name": "g"}),
        ("patch", f"/v1/folders/{f['id']}", {"name": "g"}),
        ("delete", f"/v1/folders/{f['id']}", None),
    ]
    for method, url, body in writes:
        kwargs = {"json": body} if body is not None else {}
        assert getattr(client, method)(url, **kwargs).status_code == 403, (method, url)

    as_role("viewer")
    assert client.get("/v1/collections").status_code == 200
    assert client.get(f"/v1/collections/{c['id']}").status_code == 200
