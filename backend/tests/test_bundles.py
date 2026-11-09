"""GL-122: collection bundle export + import (Phase 4, D12, D13, C4)."""

from __future__ import annotations

import io
import uuid
import zipfile

import boto3
import pytest
import yaml
from groundline_api.config import settings
from groundline_api.db import SessionLocal
from groundline_api.models.collection import Collection
from sqlalchemy import select

COLUMNS = [
    {"key": "id", "label": "Id", "type": "text", "required": True, "is_key": True},
    {"key": "question", "label": "Question", "type": "long_text"},
    {"key": "meta", "label": "Meta", "type": "json"},
    {"key": "asked_on", "label": "Asked on", "type": "date"},
]


@pytest.fixture(scope="module")
def storage_bucket():
    bucket = f"gl122-test-{uuid.uuid4().hex[:8]}"
    client = boto3.client(
        "s3",
        endpoint_url=settings.storage_endpoint_url,
        aws_access_key_id=settings.storage_access_key,
        aws_secret_access_key=settings.storage_secret_key,
    )
    client.create_bucket(Bucket=bucket)
    original = settings.storage_bucket
    settings.storage_bucket = bucket
    yield
    settings.storage_bucket = original
    for obj in client.list_objects_v2(Bucket=bucket).get("Contents", []):
        client.delete_object(Bucket=bucket, Key=obj["Key"])
    client.delete_bucket(Bucket=bucket)


def _tag() -> str:
    return uuid.uuid4().hex[:8]


def _build(client, cleanup) -> dict:
    """Collection with a/b nesting, an empty folder, and two datasets sharing one entry."""
    c = client.post("/v1/collections", json={"name": f"b-{_tag()}", "description": "evals"}).json()
    cleanup["collections"].append(c["id"])
    a = client.post(f"/v1/collections/{c['id']}/folders", json={"name": "a"}).json()
    b = client.post(f"/v1/collections/{c['id']}/folders", json={"name": "b", "parent_id": a["id"]}).json()
    client.post(f"/v1/collections/{c['id']}/folders", json={"name": "empty"})
    train = client.post(
        "/v1/datasets",
        json={"name": "train", "collection_id": c["id"], "folder_id": b["id"], "columns": COLUMNS,
              "description": "training cases"},
    ).json()
    test = client.post(
        "/v1/datasets",
        json={"name": "test", "collection_id": c["id"], "store_schema_id": train["store_schema"]["id"]},
    ).json()
    rows = {
        train["id"]: [
            {"id": "t1", "question": "Line one\nline two", "meta": {"z": 1, "a": [1, 2]}, "asked_on": "2099-01-02"},
            {"id": "t2", "question": "Short"},
        ],
        test["id"]: [{"id": "x1", "meta": {"nested": {"k": "v"}}}],
    }
    for dataset_id, items in rows.items():
        for data in items:
            resp = client.post(f"/v1/datasets/{dataset_id}/rows", json={"data": data})
            assert resp.status_code == 201, resp.text
    return {"collection": c, "train": train, "test": test, "rows": rows}


def _export(client, cid, source="draft"):
    resp = client.get(f"/v1/collections/{cid}/export", params={"source": source})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    return resp


def _post(client, url, content: bytes, name: str | None = None):
    data = {"name": name} if name else {}
    return client.post(url, files={"file": ("bundle.zip", content, "application/zip")}, data=data)


def _rows_by_key(client, dataset_id) -> dict:
    return {r["data"]["id"]: r["data"] for r in client.get(f"/v1/datasets/{dataset_id}/rows").json()}


def _zip(files: dict[str, bytes | str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def _minimal(name: str, records: str = "- id: r1\n") -> dict[str, str]:
    return {
        f"{name}/collection.yaml": f"format: groundline-collection/1\nname: {name}\n",
        f"{name}/_store/s.yaml": "columns:\n- key: id\n  label: Id\n  type: text\n  required: true\n  is_key: true\n",
        f"{name}/d.yaml": records,
        f"{name}/d.meta.yaml": "store_schema: s\n",
    }


def test_round_trip_draft(client, as_role, cleanup):
    as_role("editor")
    built = _build(client, cleanup)
    c = built["collection"]
    content = _export(client, c["id"]).content

    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        names = set(zf.namelist())
        manifest = yaml.safe_load(zf.read(f"{c['name']}/collection.yaml"))
    assert f"{c['name']}/_store/train.yaml" in names
    assert f"{c['name']}/a/b/train.yaml" in names and f"{c['name']}/a/b/train.meta.yaml" in names
    assert manifest["format"] == "groundline-collection/1" and manifest["source"] == "draft"
    assert manifest["folders"] == ["a", "empty", "a/b"]

    new_name = f"copy-{_tag()}"
    preview = _post(client, "/v1/collections/import/preview", content, new_name)
    assert preview.status_code == 200, preview.text
    assert preview.json()["ok"] and not preview.json()["name_taken"]
    assert {d["path"]: d["rows"] for d in preview.json()["datasets"]} == {"a/b/train": 2, "test": 1}

    resp = _post(client, "/v1/collections/import", content, new_name)
    assert resp.status_code == 201, resp.text
    copy = resp.json()
    cleanup["collections"].append(copy["id"])
    assert copy["name"] == new_name and copy["dataset_count"] == 2

    original = client.get(f"/v1/collections/{c['id']}").json()
    imported = client.get(f"/v1/collections/{copy['id']}").json()

    def tree(t):
        by_id = {f["id"]: f for f in t["folders"]}

        def path(fid):
            parts = []
            while fid:
                parts.append(by_id[fid]["name"])
                fid = by_id[fid]["parent_id"]
            return "/".join(reversed(parts))

        return (
            sorted(path(f["id"]) for f in t["folders"]),
            sorted((path(d["folder_id"]), d["name"], d["store_schema_name"], d["row_count"]) for d in t["datasets"]),
        )

    assert tree(original) == tree(imported)
    hashes = lambda cid: {e["name"]: e["content_hash"] for e in client.get(f"/v1/collections/{cid}/schemas").json()}
    assert hashes(c["id"]) == hashes(copy["id"])

    for ds in imported["datasets"]:
        source_id = built["train"]["id"] if ds["name"] == "train" else built["test"]["id"]
        assert _rows_by_key(client, ds["id"]) == _rows_by_key(client, source_id)
        rows = client.get(f"/v1/datasets/{ds['id']}/rows").json()
        assert {r["status"] for r in rows} == {"draft"}
    imported_train = next(d for d in imported["datasets"] if d["name"] == "train")
    assert client.get(f"/v1/datasets/{imported_train['id']}").json()["description"] == "training cases"


def test_latest_reads_versions_and_skips(client, as_role, cleanup, storage_bucket):
    as_role("editor")
    built = _build(client, cleanup)
    train_id = built["train"]["id"]
    assert client.post(f"/v1/datasets/{train_id}/versions", json={"include_unapproved": True}).status_code == 201
    row = next(r for r in client.get(f"/v1/datasets/{train_id}/rows").json() if r["data"]["id"] == "t2")
    resp = client.patch(
        f"/v1/datasets/{train_id}/rows/{row['id']}",
        headers={"If-Match": str(row["rev"])},
        json={"data": {**row["data"], "question": "edited after the cut"}},
    )
    assert resp.status_code == 200, resp.text

    as_role("viewer")
    resp = _export(client, built["collection"]["id"], "latest")
    assert resp.headers["x-groundline-skipped"] == '["test"]'
    name = built["collection"]["name"]
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        manifest = yaml.safe_load(zf.read(f"{name}/collection.yaml"))
        records = yaml.safe_load(zf.read(f"{name}/a/b/train.yaml"))
        assert f"{name}/test.yaml" not in zf.namelist()
    assert manifest["source"] == "latest"
    assert manifest["skipped"] == [{"path": "test", "reason": "no versions"}]
    assert manifest["datasets"] == [{"path": "a/b/train", "store_schema": "train", "version": 1}]
    assert {r["id"]: r.get("question") for r in records}["t2"] == "Short"


def test_import_conflicts_and_bad_bundles(client, as_role, cleanup):
    as_role("editor")
    built = _build(client, cleanup)
    c = built["collection"]
    content = _export(client, c["id"]).content

    preview = _post(client, "/v1/collections/import/preview", content)
    assert preview.status_code == 200 and preview.json()["name_taken"] is True
    resp = _post(client, "/v1/collections/import", content)
    assert resp.status_code == 409
    assert resp.json()["detail"] == f"collection '{c['name']}' already exists — pass a new name"

    name = f"bad-{_tag()}"
    bad_format = _minimal(name)
    bad_format[f"{name}/collection.yaml"] = f"format: groundline-collection/2\nname: {name}\n"
    resp = _post(client, "/v1/collections/import", _zip(bad_format))
    assert resp.status_code == 422 and "unsupported format" in resp.json()["detail"]

    # One invalid row (missing key): preview reports it, commit refuses, nothing created.
    invalid = _zip(_minimal(name, "- id: r1\n- other: x\n"))
    preview = _post(client, "/v1/collections/import/preview", invalid)
    assert preview.status_code == 200
    report = preview.json()
    assert report["ok"] is False and report["datasets"][0]["error_count"] >= 1
    resp = _post(client, "/v1/collections/import", invalid)
    assert resp.status_code == 422 and resp.json()["ok"] is False
    db = SessionLocal()
    try:
        assert db.scalar(select(Collection).where(Collection.name == name)) is None
    finally:
        db.close()

    resp = _post(client, "/v1/collections/import", _zip(_minimal(name)))
    assert resp.status_code == 201, resp.text
    cleanup["collections"].append(resp.json()["id"])


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda f, n: {**f, "../evil.yaml": "x"}, "'..' in path"),
        (lambda f, n: {**f, "/abs.yaml": "x"}, "absolute"),
        (lambda f, n: {**f, f"{n}/Bad_Folder/d.yaml": "- id: a\n", f"{n}/Bad_Folder/d.meta.yaml": "store_schema: s\n"}, "not a valid name"),
    ],
)
def test_unsafe_bundles_are_refused(client, as_role, mutate, message):
    as_role("editor")
    name = f"u-{_tag()}"
    resp = _post(client, "/v1/collections/import/preview", _zip(mutate(_minimal(name), name)))
    assert resp.status_code == 422
    assert message in resp.json()["detail"]


def test_oversize_uncompressed_is_refused(client, as_role, monkeypatch):
    as_role("editor")
    name = f"o-{_tag()}"
    monkeypatch.setattr(settings, "bundle_max_uncompressed_bytes", 100)
    files = _minimal(name, "- id: r1\n" + "# padding\n" * 50)
    resp = _post(client, "/v1/collections/import/preview", _zip(files))
    assert resp.status_code == 422 and "uncompressed" in resp.json()["detail"]


def test_bundle_rbac(client, as_role, cleanup):
    as_role("editor")
    c = client.post("/v1/collections", json={"name": f"r-{_tag()}"}).json()
    cleanup["collections"].append(c["id"])
    as_role("viewer")
    content = _export(client, c["id"]).content
    as_role("annotator")
    assert _post(client, "/v1/collections/import/preview", content, f"n-{_tag()}").status_code == 403
    assert _post(client, "/v1/collections/import", content, f"n-{_tag()}").status_code == 403


def test_round_trip_empty_dataset_and_empty_record(client, as_role, cleanup):
    """GL-133: the import-wizard blocks live in the routes, so bundle import still
    accepts a dataset with no rows (required column, no header) and a `{}` record."""
    as_role("editor")
    c = client.post("/v1/collections", json={"name": f"b-{_tag()}"}).json()
    cleanup["collections"].append(c["id"])
    required = [{"key": "id", "label": "Id", "type": "text", "required": True}]
    optional = [{"key": "note", "label": "Note", "type": "text"}]
    empty = client.post(
        "/v1/datasets", json={"name": "empty", "collection_id": c["id"], "columns": required}
    )
    assert empty.status_code == 201, empty.text
    loose = client.post(
        "/v1/datasets", json={"name": "loose", "collection_id": c["id"], "columns": optional}
    ).json()
    resp = client.post(f"/v1/datasets/{loose['id']}/rows", json={"data": {}})
    assert resp.status_code == 201, resp.text

    content = _export(client, c["id"]).content
    new_name = f"copy-{_tag()}"
    resp = _post(client, "/v1/collections/import", content, new_name)
    assert resp.status_code == 201, resp.text
    copy = resp.json()
    cleanup["collections"].append(copy["id"])
    assert copy["dataset_count"] == 2
