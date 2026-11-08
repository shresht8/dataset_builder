"""GL-118 / GL-124: collections, folders, store, paths, lock ids, pull/push (mocked HTTP)."""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile

import httpx
import pytest
import yaml
from groundline_cli.main import app
from groundline_schema import schema_hash
from groundline_schema.bundle import (
    Bundle,
    BundleDataset,
    read_dir,
    read_zip,
    write_zip,
)
from typer.testing import CliRunner

runner = CliRunner()

KEYED = [
    {"key": "id", "label": "Id", "type": "text", "required": True, "is_key": True},
    {"key": "q", "label": "Q", "type": "text", "required": False},
]


class FakeApi:
    """A tiny in-memory Groundline: just enough of the Phase 4 API for the CLI."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.collections: dict[str, dict] = {}
        self.folders: dict[str, dict] = {}
        self.store: dict[str, dict] = {}
        self.datasets: dict[str, dict] = {}
        self.sync_overrides: dict[str, dict] = {}
        self.imported: list[bytes] = []
        self.extra = None  # an extra handler tried first (returns None to fall through)
        self.dry_run: dict = {"ok": True}  # what a schema write with ?dry_run=true answers (GL-134)
        self._n = 0

    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    # -- builders ----------------------------------------------------------------
    def add_collection(self, name: str) -> dict:
        c = {"id": self._id("c"), "name": name, "description": None}
        self.collections[c["id"]] = c
        return c

    def add_folder(self, cid: str, name: str, parent_id: str | None = None) -> dict:
        f = {"id": self._id("f"), "collection_id": cid, "name": name, "parent_id": parent_id}
        self.folders[f["id"]] = f
        return f

    def add_entry(self, cid: str, name: str, columns: list[dict]) -> dict:
        e = {"id": self._id("s"), "collection_id": cid, "name": name, "columns": columns,
             "content_hash": schema_hash(columns)}
        self.store[e["id"]] = e
        return e

    def add_dataset(self, name, cid=None, fid=None, sid=None, columns=None, rows=None) -> dict:
        d = {"id": self._id("d"), "name": name, "collection_id": cid, "folder_id": fid,
             "store_schema_id": sid, "description": None,
             "columns": columns or (self.store[sid]["columns"] if sid else []), "rows": rows or []}
        self.datasets[d["id"]] = d
        return d

    # -- views -------------------------------------------------------------------
    def _folder_path(self, fid):
        parts = []
        while fid:
            parts.append(self.folders[fid]["name"])
            fid = self.folders[fid]["parent_id"]
        return list(reversed(parts))

    def path(self, d) -> str:
        if not d["collection_id"]:
            return d["name"]
        c = self.collections[d["collection_id"]]["name"]
        return "/".join([c, *self._folder_path(d["folder_id"]), d["name"]])

    def _ref(self, d, created=False):
        if not d["store_schema_id"]:
            return None
        return {"id": d["store_schema_id"], "name": self.store[d["store_schema_id"]]["name"], "created": created}

    def read(self, d, created=False):
        return {"id": d["id"], "name": d["name"], "description": d["description"], "feature_id": None,
                "collection_id": d["collection_id"], "folder_id": d["folder_id"],
                "store_schema_id": d["store_schema_id"], "path": self.path(d),
                "store_schema": self._ref(d, created)}

    def _entry_read(self, e):
        used = [{"id": d["id"], "name": d["name"], "path": self.path(d)}
                for d in self.datasets.values() if d["store_schema_id"] == e["id"]]
        return {**e, "used_by": used}

    def _resolve_or_add(self, cid, columns, name):
        h = schema_hash(columns)
        for e in self.store.values():
            if e["collection_id"] == cid and e["content_hash"] == h:
                return e, False
        names = {e["name"] for e in self.store.values() if e["collection_id"] == cid}
        final, n = name, 2
        while final in names:
            final, n = f"{name}-{n}", n + 1
        return self.add_entry(cid, final, columns), True

    # -- transport ---------------------------------------------------------------
    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.extra is not None:
            response = self.extra(request)
            if response is not None:
                return response
        p, m = request.url.path.removeprefix("/v1"), request.method
        body = json.loads(request.content) if request.content and request.headers.get(
            "content-type", "").startswith("application/json") else None

        if p == "/collections" and m == "GET":
            return httpx.Response(200, json=[
                {**c, "dataset_count": sum(d["collection_id"] == c["id"] for d in self.datasets.values())}
                for c in self.collections.values()])
        if p == "/collections" and m == "POST":
            if any(c["name"] == body["name"] for c in self.collections.values()):
                return httpx.Response(409, json={"detail": f"collection '{body['name']}' already exists"})
            return httpx.Response(201, json=self.add_collection(body["name"]))
        if p == "/collections/import/preview":
            return httpx.Response(200, json={"ok": True, "errors": [], "store": [], "datasets": [
                {"path": "x", "rows": 1, "store_schema": "s", "errors": []}]})
        if p == "/collections/import":
            self.imported.append(request.content)
            zipped = request.content[request.content.index(b"PK"):]
            bundle = read_zip(zipped, 10**8)
            c = self.add_collection(bundle.name)
            entries = {n: self.add_entry(c["id"], n, cols) for n, cols in bundle.store.items()}
            for ds in bundle.datasets:
                self.add_dataset(ds.name, c["id"], None, entries[ds.store_schema]["id"],
                                 rows=yaml.safe_load(ds.content))
            return httpx.Response(201, json={**c, "dataset_count": len(bundle.datasets)})
        match = re.fullmatch(r"/collections/(c\d+)(/.*)?", p)
        if match:
            cid, rest = match.group(1), match.group(2) or ""
            if rest == "" and m == "GET":
                c = self.collections[cid]
                return httpx.Response(200, json={
                    **c, "dataset_count": 0,
                    "folders": [f for f in self.folders.values() if f["collection_id"] == cid],
                    "datasets": [{**self.read(d), "row_count": len(d["rows"]), "version_count": 0,
                                  "store_schema_name": self.store[d["store_schema_id"]]["name"]}
                                 for d in self.datasets.values() if d["collection_id"] == cid]})
            if rest == "" and m == "DELETE":
                if any(d["collection_id"] == cid for d in self.datasets.values()):
                    return httpx.Response(409, json={"detail": "collection holds datasets"})
                del self.collections[cid]
                return httpx.Response(204)
            if rest == "/folders":
                return httpx.Response(201, json=self.add_folder(cid, body["name"], body["parent_id"]))
            if rest == "/schemas" and m == "GET":
                return httpx.Response(200, json=[self._entry_read(e) for e in self.store.values()
                                                 if e["collection_id"] == cid])
            if rest == "/schemas" and m == "POST":
                return httpx.Response(201, json=self._entry_read(self.add_entry(cid, body["name"], body["columns"])))
            if rest == "/export":
                return httpx.Response(200, content=self.export(cid))
            em = re.fullmatch(r"/schemas/(s\d+)(/duplicate)?", rest)
            if em:
                e = self.store[em.group(1)]
                used = self._entry_read(e)["used_by"]
                if em.group(2):
                    return httpx.Response(201, json=self._entry_read(self.add_entry(cid, body["name"], e["columns"])))
                if used and m in ("PUT", "DELETE"):
                    return httpx.Response(409, json={
                        "detail": f"schema '{e['name']}' is used by {len(used)} dataset(s) "
                                  f"({', '.join(u['path'] for u in used)}) and can't be changed",
                        "used_by": used})
                if m == "PUT":
                    e["columns"] = body["columns"]
                    return httpx.Response(200, json=self._entry_read(e))
                if m == "DELETE":
                    del self.store[e["id"]]
                    return httpx.Response(204)
        fm = re.fullmatch(r"/folders/(f\d+)", p)
        if fm and m == "DELETE":
            del self.folders[fm.group(1)]
            return httpx.Response(204)

        if p == "/datasets" and m == "GET":
            cid = request.url.params.get("collection_id")
            return httpx.Response(200, json=[self.read(d) for d in self.datasets.values()
                                             if not cid or d["collection_id"] == cid])
        if p == "/datasets" and m == "POST":
            created, sid = False, body.get("store_schema_id")
            if body.get("collection_id") and body.get("columns") is not None:
                entry, created = self._resolve_or_add(body["collection_id"], body["columns"], body["name"])
                sid = entry["id"]
            d = self.add_dataset(body["name"], body.get("collection_id"), body.get("folder_id"), sid,
                                 columns=body.get("columns"))
            return httpx.Response(201, json=self.read(d, created))
        if p == "/datasets/resolve":
            want = request.url.params["path"]
            for d in self.datasets.values():
                if self.path(d) == want:
                    return httpx.Response(200, json=self.read(d))
            return httpx.Response(404, json={"detail": f"no dataset '{want}'"})
        dm = re.fullmatch(r"/datasets/(d\d+)(/.*)?", p)
        if dm:
            d, rest = self.datasets[dm.group(1)], dm.group(2) or ""
            if rest == "":
                return httpx.Response(200, json=self.read(d))
            if rest == "/schema" and m == "GET":
                return httpx.Response(200, json={"columns": d["columns"], "store_schema": self._ref(d)})
            if rest == "/schema" and m == "PUT" and request.url.params.get("dry_run"):
                return httpx.Response(200, json=self.dry_run)
            if rest == "/schema/apply" and request.url.params.get("dry_run"):
                return httpx.Response(200, json=self.dry_run)
            if rest == "/schema" and m == "PUT":
                created = False
                if d["collection_id"]:
                    entry, created = self._resolve_or_add(d["collection_id"], body["columns"], d["name"])
                    d["store_schema_id"] = entry["id"]
                d["columns"] = body["columns"]
                return httpx.Response(200, json={"columns": d["columns"], "store_schema": self._ref(d, created)})
            if rest == "/schema/apply":
                d["store_schema_id"] = body["store_schema_id"]
                d["columns"] = self.store[body["store_schema_id"]]["columns"]
                return httpx.Response(200, json={"columns": d["columns"], "store_schema": self._ref(d)})
            if rest == "/move":
                was = d["collection_id"]
                d["collection_id"], d["folder_id"] = body["collection_id"], body["folder_id"]
                created = False
                if d["collection_id"] is None:
                    d["store_schema_id"] = None
                elif d["collection_id"] != was:
                    entry, created = self._resolve_or_add(d["collection_id"], d["columns"], d["name"])
                    d["store_schema_id"] = entry["id"]
                return httpx.Response(200, json=self.read(d, created))
            if rest == "/rows":
                return httpx.Response(200, json=[{"id": f"r{i}", "rev": 1, "data": r} for i, r in enumerate(d["rows"])])
            if rest == "/sync":
                dry = b'name="dry_run"\r\n\r\ntrue' in request.content
                result = {"applied": not dry, "created": 0, "updated": 0, "unchanged": len(d["rows"]),
                          "server_newer": 0, "not_in_file": 0, "changes": [], "conflicts": [],
                          "errors": [], "ignored_paths": [], "server_newer_keys": [],
                          "deleted_keys": [], "revs": {}, "bases": {}}
                override = self.sync_overrides.get(self.path(d))
                if override:
                    result.update(override)
                    if result["conflicts"] or result["errors"]:
                        result["applied"] = False
                return httpx.Response(200, json=result)
            if rest == "/versions":
                return httpx.Response(200, json=[])
        return httpx.Response(404, json={"detail": f"not found: {m} {p}"})

    def calls(self, method: str, pattern: str) -> list[httpx.Request]:
        """Real calls; a dry run (`?dry_run=true` on a schema write) is not one."""
        return [
            r for r in self.requests
            if r.method == method and re.fullmatch(pattern, r.url.path) and not r.url.params.get("dry_run")
        ]

    def export(self, cid: str) -> bytes:
        c = self.collections[cid]
        bundle = Bundle(name=c["name"], folders=sorted(
            "/".join(self._folder_path(f["id"])) for f in self.folders.values() if f["collection_id"] == cid))
        for e in self.store.values():
            if e["collection_id"] == cid:
                bundle.store[e["name"]] = e["columns"]
        for d in self.datasets.values():
            if d["collection_id"] == cid:
                rel = self.path(d).split("/", 1)[1]
                bundle.datasets.append(BundleDataset(
                    path=rel, store_schema=self.store[d["store_schema_id"]]["name"],
                    content=yaml.safe_dump(d["rows"]).encode()))
        return write_zip(bundle)


@pytest.fixture
def fake(monkeypatch, install_transport):
    monkeypatch.setenv("GROUNDLINE_API_URL", "http://api.test")
    monkeypatch.setenv("GROUNDLINE_TOKEN", "tok")
    api = FakeApi()
    install_transport(api)
    return api


def _run(*args, input=None):
    return runner.invoke(app, list(args), input=input)


def _schema_file(tmp_path, columns=KEYED, name="s.yaml"):
    path = tmp_path / name
    path.write_text(yaml.safe_dump({"columns": columns}), encoding="utf-8")
    return path


# --- collections / folders / datasets -------------------------------------------------


def test_collections_and_folders(fake):
    assert _run("collections", "create", "evals").exit_code == 0
    result = _run("collections", "create", "evals")
    assert result.exit_code == 1 and "already exists" in result.output
    assert _run("folders", "create", "evals/a").exit_code == 0
    assert _run("folders", "create", "evals/a/b").exit_code == 0
    result = _run("folders", "create", "evals/missing/b")
    assert result.exit_code == 1 and "no folder 'missing'" in result.output
    assert {f["name"] for f in fake.folders.values()} == {"a", "b"}
    assert "evals" in _run("collections", "list").output
    assert _run("folders", "delete", "evals/a/b").exit_code == 0
    assert _run("collections", "delete", "evals", "--yes").exit_code == 0
    assert not fake.collections


def test_datasets_create_in_a_collection(fake, tmp_path):
    c = fake.add_collection("evals")
    fake.add_folder(c["id"], "f")
    fake.add_folder(c["id"], "g")
    schema = _schema_file(tmp_path)

    result = _run("datasets", "create", "evals/f/train", "--schema", str(schema))
    assert result.exit_code == 0, result.output
    assert "created evals/f/train with 2 columns" in result.output
    assert "added to store as train" in result.output

    result = _run("datasets", "create", "evals/g/train", "--from-store", "train")
    assert result.exit_code == 0, result.output
    assert "using store schema train" in result.output

    result = _run("datasets", "create", "evals/g/x", "--from-store", "nope")
    assert result.exit_code == 1 and "unknown store schema" in result.output
    result = _run("datasets", "create", "evals/g/x")
    assert result.exit_code == 1 and "--schema FILE, or --from-store" in result.output
    result = _run("datasets", "create", "plain", "--from-store", "train")
    assert result.exit_code == 1 and "needs a collection path" in result.output

    listed = _run("datasets", "list", "--collection", "evals").output
    assert "evals/f/train" in listed and "evals/g/train" in listed


def test_datasets_move(fake):
    c = fake.add_collection("evals")
    fake.add_folder(c["id"], "f")
    d = fake.add_dataset("loose", columns=KEYED)
    result = _run("datasets", "move", "loose", "--to", "evals/f")
    assert result.exit_code == 0, result.output
    assert "moved loose -> evals/f/loose" in result.output
    assert "added to store as loose" in result.output
    result = _run("datasets", "move", "evals/f/loose", "--unfiled")
    assert result.exit_code == 0 and "-> loose" in result.output
    assert d["store_schema_id"] is None
    result = _run("datasets", "move", "loose")
    assert result.exit_code == 1


# --- store / schema -------------------------------------------------------------------


def test_store_commands(fake, tmp_path):
    c = fake.add_collection("evals")
    used = fake.add_entry(c["id"], "used", KEYED)
    fake.add_dataset("train", c["id"], sid=used["id"])

    listed = _run("store", "list", "evals").output
    assert "used" in listed and "evals/train" in listed

    out = tmp_path / "used.yaml"
    assert _run("store", "pull", "evals", "used", "-o", str(out)).exit_code == 0
    assert yaml.safe_load(out.read_text())["columns"][0]["key"] == "id"

    result = _run("store", "push", "evals", "used", str(out))
    assert result.exit_code == 1
    assert "API error 409" in result.output and "evals/train" in result.output

    assert "created store schema fresh" in _run("store", "push", "evals", "fresh", str(out)).output
    assert "updated store schema fresh" in _run("store", "push", "evals", "fresh", str(out)).output
    assert _run("store", "duplicate", "evals", "used", "used-2").exit_code == 0
    result = _run("store", "delete", "evals", "used", "--yes")
    assert result.exit_code == 1 and "409" in result.output
    assert _run("store", "delete", "evals", "used-2", "--yes").exit_code == 0
    assert {e["name"] for e in fake.store.values()} == {"used", "fresh"}


def test_schema_push_reports_store_outcome_and_apply(fake, tmp_path):
    c = fake.add_collection("evals")
    entry = fake.add_entry(c["id"], "train", KEYED)
    fake.add_dataset("train", c["id"], sid=entry["id"])
    wider = _schema_file(tmp_path, KEYED + [{"key": "n", "label": "N", "type": "number", "required": False}])

    result = _run("schema", "push", "evals/train", str(wider))
    assert result.exit_code == 0, result.output
    assert "added: n" in result.output and "store: added train-2" in result.output

    result = _run("schema", "apply", "evals/train", "train", input="y\n")
    assert result.exit_code == 0, result.output
    assert "archived: n" in result.output and "now uses store schema train" in result.output
    assert len(fake.calls("POST", r"/v1/datasets/d\d+/schema/apply")) == 1

    result = _run("schema", "apply", "evals/train", "train-2", input="n\n")
    assert result.exit_code == 0 and "added: n" in result.output
    loose = fake.add_dataset("loose", columns=KEYED)
    result = _run("schema", "apply", "loose", "train")
    assert result.exit_code == 1 and "not in a collection" in result.output
    assert loose["store_schema_id"] is None


def test_schema_apply_dry_run_and_refusal(fake):
    c = fake.add_collection("evals")
    entry = fake.add_entry(c["id"], "train", KEYED)
    fake.add_entry(c["id"], "wide", KEYED + [{"key": "n", "label": "N", "type": "number", "required": False}])
    ds = fake.add_dataset("train", c["id"], sid=entry["id"])

    result = _run("schema", "apply", "evals/train", "wide", "--dry-run")
    assert result.exit_code == 0 and "ok: no stored row would break" in result.output
    assert ds["store_schema_id"] == entry["id"]
    assert not fake.calls("POST", r"/v1/datasets/d\d+/schema/apply")

    fake.dry_run = {"ok": False, "detail": "Column 'n' would be required but has no value in 2 rows"}
    result = _run("schema", "apply", "evals/train", "wide", "--yes")
    assert result.exit_code == 1 and "Column 'n' would be required" in result.output
    assert not fake.calls("POST", r"/v1/datasets/d\d+/schema/apply")


# --- lock ---------------------------------------------------------------------------


JSONL = b'{"data":{"id":"a"},"id":"r1","status":"approved"}\n'
HASH = "sha256:" + hashlib.sha256(JSONL).hexdigest()


def _versions(fake):
    def handler(request):
        path = request.url.path
        if re.fullmatch(r"/v1/datasets/d\d+/versions/1", path):
            return httpx.Response(200, content=JSONL)
        if path.endswith("/versions/1/manifest"):
            return httpx.Response(200, json={"dataset": "train", "version": 1, "content_hash": HASH,
                                             "row_count": 1, "created_at": "2099-01-01T00:00:00Z",
                                             "created_by": "x"})
        if path.endswith("/versions/1/jsonschema"):
            return httpx.Response(200, json={"properties": {"data": {"required": []}}})
        return None

    return handler


def test_pull_by_path_and_lock_after_move(fake, tmp_path):
    c = fake.add_collection("evals")
    f = fake.add_folder(c["id"], "f")
    d = fake.add_dataset("train", c["id"], f["id"], sid=fake.add_entry(c["id"], "s", KEYED)["id"])
    fake.extra = _versions(fake)
    lock = tmp_path / "groundline.lock"

    result = _run("pull", "evals/f/train@v1", "-o", str(tmp_path / "out"), "--lock-file", str(lock))
    assert result.exit_code == 0, result.output
    assert (tmp_path / "out" / "evals" / "f" / "train" / "v1" / "train.schema.json").exists()
    entry = json.loads(lock.read_text())["datasets"]["evals/f/train"]
    assert entry == {"version": 1, "content_hash": HASH, "dataset_id": d["id"]}

    d["folder_id"] = None  # moved to the collection root
    result = _run("pull", "--lock", "--lock-file", str(lock), "-o", str(tmp_path / "again"))
    assert result.exit_code == 0, result.output
    assert "has moved to evals/train" in result.output
    assert (tmp_path / "again" / "evals" / "f" / "train" / "v1" / "rows.jsonl").read_bytes() == JSONL


def test_old_lock_without_id_restores_by_path(fake, tmp_path):
    fake.add_dataset("train", columns=KEYED)
    fake.extra = _versions(fake)
    lock = tmp_path / "groundline.lock"
    lock.write_text(json.dumps({"datasets": {"train": {"version": 1, "content_hash": HASH}}}))
    result = _run("pull", "--lock", "--lock-file", str(lock), "-o", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert "moved" not in result.output


# --- collections pull / push --------------------------------------------------------------


def _populated(fake):
    c = fake.add_collection("evals")
    f = fake.add_folder(c["id"], "f")
    entry = fake.add_entry(c["id"], "s", KEYED)
    fake.add_dataset("train", c["id"], f["id"], entry["id"], rows=[{"id": "a", "q": "x"}])
    fake.add_dataset("test", c["id"], None, entry["id"], rows=[{"id": "b"}])
    return c


def test_collections_pull_writes_layout_and_state(fake, tmp_path):
    _populated(fake)
    out = tmp_path / "evals"
    result = _run("collections", "pull", "evals", "-o", str(out))
    assert result.exit_code == 0, result.output
    assert "pulled 2 dataset(s) of evals (draft)" in result.output
    assert yaml.safe_load((out / "collection.yaml").read_text())["format"] == "groundline-collection/1"
    assert (out / "_store" / "s.yaml").exists()
    assert yaml.safe_load((out / "f" / "train.yaml").read_text()) == [{"id": "a", "q": "x"}]
    assert yaml.safe_load((out / "f" / "train.meta.yaml").read_text())["store_schema"] == "s"
    state = out / "f" / ".groundline" / "rows" / "evals" / "f" / "train" / "train.yaml.json"
    assert json.loads(state.read_text())["revs"] == {"a": 1}
    assert [d.path for d in read_dir(out).datasets] == ["f/train", "test"]


def test_push_to_a_new_collection_uses_the_import_api(fake, tmp_path):
    root = tmp_path / "fresh"
    from groundline_schema.bundle import write_dir

    write_dir(Bundle(name="fresh", store={"s": KEYED},
                     datasets=[BundleDataset(path="x", store_schema="s", content=b"- id: r1\n")]), root)
    result = _run("collections", "push", str(root), "--dry-run")
    assert result.exit_code == 0, result.output
    assert "will be created" in result.output and not fake.imported
    result = _run("collections", "push", str(root))
    assert result.exit_code == 0, result.output
    (sent,) = fake.imported
    assert b"collection.yaml" in sent and b"fresh/x.yaml" in sent


def test_push_existing_creates_missing_and_syncs(fake, tmp_path):
    _populated(fake)
    out = tmp_path / "evals"
    assert _run("collections", "pull", "evals", "-o", str(out)).exit_code == 0
    (out / "g").mkdir()
    (out / "g" / "new.yaml").write_text("- id: n1\n")
    (out / "g" / "new.meta.yaml").write_text("store_schema: s\n")
    changed = yaml.safe_load((out / "_store" / "s.yaml").read_text())
    changed["columns"][1]["label"] = "Changed"
    (out / "_store" / "s.yaml").write_text(yaml.safe_dump(changed))

    result = _run("collections", "push", str(out))
    assert result.exit_code == 0, result.output
    assert "store schema s differs" in result.output
    assert "+ folder g" in result.output and "+ dataset g/new" in result.output
    assert not fake.calls("PUT", r"/v1/collections/c\d+/schemas/s\d+")  # never edited
    assert any(f["name"] == "g" for f in fake.folders.values())
    assert any(d["name"] == "new" for d in fake.datasets.values())
    applied = [r for r in fake.calls("POST", r"/v1/datasets/d\d+/sync") if b'name="dry_run"\r\n\r\nfalse' in r.content]
    assert len(applied) == 3


def test_push_conflict_anywhere_applies_nothing(fake, tmp_path):
    _populated(fake)
    out = tmp_path / "evals"
    assert _run("collections", "pull", "evals", "-o", str(out)).exit_code == 0
    fake.sync_overrides["evals/test"] = {
        "conflicts": [{"key": "b", "server_rev": 2, "your_rev": 1, "fields": {}}]}
    (out / "h").mkdir()
    (out / "h" / "other.yaml").write_text("- id: z\n")
    (out / "h" / "other.meta.yaml").write_text("store_schema: s\n")

    result = _run("collections", "push", str(out))
    assert result.exit_code == 1
    assert "== test" in result.output and "conflicts (1)" in result.output
    assert "nothing applied" in result.output
    assert not [r for r in fake.calls("POST", r"/v1/datasets/d\d+/sync")
                if b'name="dry_run"\r\n\r\nfalse' in r.content]
    assert not fake.calls("POST", r"/v1/collections/c\d+/folders")
    assert not any(d["name"] == "other" for d in fake.datasets.values())


def test_pull_refuses_to_overwrite_unpushed_edits(fake, tmp_path):
    _populated(fake)
    out = tmp_path / "evals"
    assert _run("collections", "pull", "evals", "-o", str(out)).exit_code == 0
    fake.sync_overrides["evals/f/train"] = {"changes": [{"op": "update", "key": "a", "fields": {}}]}
    result = _run("collections", "pull", "evals", "-o", str(out))
    assert result.exit_code == 1 and "f/train (1 row(s))" in result.output
    assert _run("collections", "pull", "evals", "-o", str(out), "--force").exit_code == 0


def test_bundle_zip_layout_round_trips():
    bundle = Bundle(name="z", folders=["a"], store={"s": KEYED},
                    datasets=[BundleDataset(path="a/d", store_schema="s", content=b"- id: x\n")])
    with zipfile.ZipFile(io.BytesIO(write_zip(bundle))) as zf:
        assert sorted(zf.namelist()) == ["z/_store/s.yaml", "z/a/d.meta.yaml", "z/a/d.yaml", "z/collection.yaml"]
