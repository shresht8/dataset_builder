"""Collection bundles: write and read the C4 layout (Phase 4, GL-122).

Shared by the API (zip export/import) and the CLI (`collections pull/push`,
a directory), so both read and write one format::

    collection.yaml                 format: groundline-collection/1
                                    name, description, exported_at,
                                    source: draft|latest, folders: [...],
                                    datasets: [{path, store_schema, version?}],
                                    skipped: [{path, reason}]
    _store/<entry>.yaml             columns: [...]  (as `groundline schema pull`)
    <folder>/…/<dataset>.yaml       records, as `rows pull` writes them
    <folder>/…/<dataset>.meta.yaml  description, store_schema: <entry>

Paths inside a bundle are relative to the collection, so a bundle can be
imported under another name. `folders` lists every folder (so empty folders
survive a round trip), parents before children. A zip holds the same files
under one top-level `<collection>/` directory.

Reading is strict: YAML is read with the safe loader, `format` must match,
every path segment must match the name pattern, and a zip entry with `..`, an
absolute path or a symlink is refused, as is a zip over the uncompressed cap.
Hidden files and directories (a leading `.`, e.g. the CLI's `.groundline/`
sync state) are ignored. Dataset records are returned as raw bytes: the API
and the CLI parse them with the same reader as import and sync.
"""

from __future__ import annotations

import io
import re
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from pydantic import ValidationError

from groundline_schema.column_types import Column
from groundline_schema.paths import nest
from groundline_schema.render import BlockStyleDumper

FORMAT = "groundline-collection/1"
STORE_DIR = "_store"
COLLECTION_FILE = "collection.yaml"
META_SUFFIX = ".meta.yaml"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,99}$")


class BundleError(ValueError):
    """A bundle that can't be read: bad layout, format, name or zip entry."""


@dataclass
class BundleDataset:
    path: str  # collection-relative: "folder/…/name"
    store_schema: str
    description: str | None = None
    version: int | None = None
    content: bytes = b""  # the records file

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    @property
    def folder(self) -> str | None:
        return self.path.rsplit("/", 1)[0] if "/" in self.path else None


@dataclass
class Bundle:
    name: str
    description: str | None = None
    source: str = "draft"
    exported_at: str | None = None
    folders: list[str] = field(default_factory=list)
    store: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    datasets: list[BundleDataset] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)


# --- Writing -----------------------------------------------------------------


def _sorted_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _sorted_keys(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [_sorted_keys(item) for item in value]
    return value


def records_for(rows: list[dict[str, Any]], columns: list[dict[str, Any]]) -> list[dict]:
    """Nested records from flat row data, as `rows pull` writes them.

    Key column first, then schema order; null fields left out; keys inside
    `json` values sorted (Postgres doesn't keep their order).
    """
    key = next((c["key"] for c in columns if c.get("is_key")), None)
    order = ([key] if key else []) + [c["key"] for c in columns if c["key"] != key]
    json_keys = {c["key"] for c in columns if c["type"] == "json"}
    records = []
    for data in rows:
        flat = {
            k: _sorted_keys(data[k]) if k in json_keys else data[k]
            for k in order
            if data.get(k) is not None
        }
        records.append(nest(flat))
    return records


def dump_yaml(document: Any) -> str:
    return yaml.dump(
        document, Dumper=BlockStyleDumper, allow_unicode=True, sort_keys=False, width=4096
    )


def render_records(records: list[dict]) -> bytes:
    return dump_yaml(records).encode("utf-8")


def wire_column(column: Column | dict[str, Any]) -> dict[str, Any]:
    """A column as a schema file lists it (no server-managed fields)."""
    col = column if isinstance(column, Column) else Column.model_validate(column)
    item: dict[str, Any] = {"key": col.key, "label": col.label, "type": col.type.value}
    if col.options is not None:
        item["options"] = col.options
    item["required"] = col.required
    if col.is_key:
        item["is_key"] = True
    if col.json_schema is not None:
        item["json_schema"] = col.json_schema
    return item


def write_files(bundle: Bundle) -> dict[str, bytes]:
    """The bundle's files: collection-relative path -> bytes."""
    if any(ds.path == COLLECTION_FILE.removesuffix(".yaml") for ds in bundle.datasets):
        raise BundleError(
            "a dataset named 'collection' at the collection root would clash with "
            f"{COLLECTION_FILE}; move it into a folder first"
        )
    manifest: dict[str, Any] = {"format": FORMAT, "name": bundle.name}
    if bundle.description is not None:
        manifest["description"] = bundle.description
    if bundle.exported_at is not None:
        manifest["exported_at"] = bundle.exported_at
    manifest["source"] = bundle.source
    manifest["folders"] = list(bundle.folders)
    manifest["datasets"] = []
    for ds in bundle.datasets:
        item: dict[str, Any] = {"path": ds.path, "store_schema": ds.store_schema}
        if ds.version is not None:
            item["version"] = ds.version
        manifest["datasets"].append(item)
    manifest["skipped"] = list(bundle.skipped)

    files = {COLLECTION_FILE: dump_yaml(manifest).encode("utf-8")}
    for name, columns in bundle.store.items():
        doc = {"columns": [wire_column(c) for c in columns]}
        files[f"{STORE_DIR}/{name}.yaml"] = dump_yaml(doc).encode("utf-8")
    for ds in bundle.datasets:
        meta: dict[str, Any] = {}
        if ds.description is not None:
            meta["description"] = ds.description
        meta["store_schema"] = ds.store_schema
        files[f"{ds.path}.yaml"] = ds.content
        files[f"{ds.path}{META_SUFFIX}"] = dump_yaml(meta).encode("utf-8")
    return files


def write_zip(bundle: Bundle) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, content in write_files(bundle).items():
            zf.writestr(f"{bundle.name}/{path}", content)
    return buffer.getvalue()


def write_dir(bundle: Bundle, root: Path) -> list[Path]:
    """Write the bundle's files under `root`. Returns the paths written."""
    written = []
    for path, content in write_files(bundle).items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        written.append(target)
    return written


# --- Reading -----------------------------------------------------------------


def _check_segment(segment: str, where: str) -> None:
    if not NAME_RE.match(segment):
        raise BundleError(
            f"{where}: '{segment}' is not a valid name "
            "(lowercase letters, digits, '-' and '_'; starting with a letter or digit)"
        )


def _check_path(path: str, where: str) -> list[str]:
    segments = path.split("/")
    for segment in segments:
        _check_segment(segment, where)
    return segments


def _safe_yaml(content: bytes, where: str) -> Any:
    try:
        return yaml.safe_load(content.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise BundleError(f"{where}: not valid YAML: {exc}") from None


def _store_entry(content: bytes, where: str) -> list[dict[str, Any]]:
    doc = _safe_yaml(content, where)
    columns = doc.get("columns") if isinstance(doc, dict) else doc
    if not isinstance(columns, list):
        raise BundleError(f"{where}: expected a 'columns' list")
    out = []
    for index, column in enumerate(columns, start=1):
        try:
            out.append(wire_column(Column.model_validate(column)))
        except ValidationError as exc:
            problem = "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
            )
            raise BundleError(f"{where}: column {index}: {problem}") from None
    return out


def read_files(files: dict[str, bytes]) -> Bundle:
    """Read a bundle from collection-relative paths -> bytes."""
    files = {
        path: content
        for path, content in files.items()
        if not any(part.startswith(".") for part in path.split("/"))
    }
    if COLLECTION_FILE not in files:
        raise BundleError(f"not a collection bundle: no {COLLECTION_FILE}")
    manifest = _safe_yaml(files.pop(COLLECTION_FILE), COLLECTION_FILE)
    if not isinstance(manifest, dict):
        raise BundleError(f"{COLLECTION_FILE}: expected a mapping")
    if manifest.get("format") != FORMAT:
        raise BundleError(
            f"{COLLECTION_FILE}: unsupported format {manifest.get('format')!r} (expected {FORMAT!r})"
        )
    name = manifest.get("name")
    if not isinstance(name, str):
        raise BundleError(f"{COLLECTION_FILE}: missing 'name'")
    _check_segment(name, f"{COLLECTION_FILE} name")
    description = manifest.get("description")
    versions = {
        item.get("path"): item.get("version")
        for item in manifest.get("datasets") or []
        if isinstance(item, dict)
    }

    store: dict[str, list[dict[str, Any]]] = {}
    data_files: dict[str, bytes] = {}
    metas: dict[str, bytes] = {}
    for path, content in sorted(files.items()):
        if path.startswith(f"{STORE_DIR}/"):
            entry = path.removeprefix(f"{STORE_DIR}/")
            if "/" in entry or not entry.endswith(".yaml"):
                raise BundleError(f"{path}: store entries must be {STORE_DIR}/<name>.yaml")
            entry = entry.removesuffix(".yaml")
            _check_segment(entry, path)
            store[entry] = _store_entry(content, path)
        elif path.endswith(META_SUFFIX):
            metas[path.removesuffix(META_SUFFIX)] = content
        elif path.endswith(".yaml"):
            data_files[path.removesuffix(".yaml")] = content
        else:
            raise BundleError(f"{path}: unexpected file (bundles hold only .yaml files)")

    folders: list[str] = []
    for folder in manifest.get("folders") or []:
        if not isinstance(folder, str):
            raise BundleError(f"{COLLECTION_FILE}: folders must be paths")
        _check_path(folder, f"{COLLECTION_FILE} folder")
        folders.append(folder)

    datasets: list[BundleDataset] = []
    for path, content in data_files.items():
        segments = _check_path(path, f"{path}.yaml")
        for end in range(1, len(segments)):
            folder = "/".join(segments[:end])
            if folder not in folders:
                folders.append(folder)
        if path not in metas:
            raise BundleError(f"{path}.yaml: missing {path}{META_SUFFIX}")
        meta = _safe_yaml(metas.pop(path), f"{path}{META_SUFFIX}")
        if not isinstance(meta, dict) or not isinstance(meta.get("store_schema"), str):
            raise BundleError(f"{path}{META_SUFFIX}: expected 'store_schema: <entry name>'")
        version = versions.get(path)
        datasets.append(
            BundleDataset(
                path=path,
                store_schema=meta["store_schema"],
                description=meta.get("description"),
                version=version if isinstance(version, int) else None,
                content=content,
            )
        )
    if metas:
        orphan = min(metas)
        raise BundleError(f"{orphan}{META_SUFFIX}: no {orphan}.yaml beside it")

    # Parents before children.
    folders.sort(key=lambda f: (f.count("/"), f))
    return Bundle(
        name=name,
        description=description if isinstance(description, str) else None,
        source=str(manifest.get("source") or "draft"),
        exported_at=str(manifest["exported_at"]) if manifest.get("exported_at") else None,
        folders=folders,
        store=store,
        datasets=datasets,
        skipped=[s for s in manifest.get("skipped") or [] if isinstance(s, dict)],
    )


def _unsafe_entry(info: zipfile.ZipInfo) -> str | None:
    name = info.filename
    if name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name) or "\\" in name:
        return "absolute or non-portable path"
    if ".." in PurePosixPath(name).parts:
        return "'..' in path"
    if stat.S_ISLNK(info.external_attr >> 16):
        return "symlink"
    return None


def read_zip(content: bytes, max_uncompressed: int) -> Bundle:
    """Read a zipped bundle, enforcing the C4 safety rules."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise BundleError("not a zip file") from None
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        for info in infos:
            problem = _unsafe_entry(info)
            if problem:
                raise BundleError(f"refused zip entry {info.filename!r}: {problem}")
        if sum(i.file_size for i in infos) > max_uncompressed:
            raise BundleError(
                f"bundle is larger than {max_uncompressed // (1024 * 1024)} MB uncompressed"
            )
        files: dict[str, bytes] = {}
        remaining = max_uncompressed
        for info in infos:
            with zf.open(info) as fh:
                data = fh.read(remaining + 1)
            remaining -= len(data)
            if remaining < 0:  # the headers under-stated the size
                raise BundleError(
                    f"bundle is larger than {max_uncompressed // (1024 * 1024)} MB uncompressed"
                )
            files[info.filename] = data

    if COLLECTION_FILE not in files:
        tops = {name.split("/", 1)[0] for name in files}
        if len(tops) == 1 and all("/" in name for name in files):
            top = tops.pop() + "/"
            files = {name.removeprefix(top): data for name, data in files.items()}
    return read_files(files)


def read_dir(root: Path) -> Bundle:
    """Read a bundle from a directory (hidden files and directories ignored)."""
    if not (root / COLLECTION_FILE).is_file():
        raise BundleError(f"{root} is not a collection directory: no {COLLECTION_FILE}")
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if any(part.startswith(".") for part in rel.split("/")) or not path.is_file():
            continue
        files[rel] = path.read_bytes()
    return read_files(files)
