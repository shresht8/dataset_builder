"""Row files and their sync state for `rows pull` / `rows push` (GL-3.5-16).

Records are written exactly as the user keeps them: nested from dotted column
keys, key column first then schema order, nothing Groundline-specific inside.
Row revs and base hashes go to a state file beside the output, one per
dataset and rows file (pushing another file to the same dataset, e.g. a fix-up
or a wrong file, leaves this file's state alone)::

    <dir>/.groundline/rows/<dataset>/<file name>.json
    {"dataset_id", "key_column", "file", "revs": {key: rev},
     "bases": {key: row_hash}, "pulled_at"}

Pulling into an existing yaml/json file with a records key (given, or found
in the file the way import finds it) replaces only that key's list; the file's
other top-level keys are kept, plain YAML values exactly as written (so a
loader that reads `NO` as false still does). Comments and anchors/aliases are
not preserved: aliases are written out literally.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from groundline_schema.paths import nest, row_hash
from groundline_schema.records import (
    PlainScalar,
    RecordsError,
    parse_document,
    parse_records,
)
from groundline_schema.render import BlockStyleDumper

FORMATS = ("yaml", "json", "jsonl")
_EXTENSIONS = {".yaml": "yaml", ".yml": "yaml", ".json": "json", ".jsonl": "jsonl", ".ndjson": "jsonl"}


class RowFileError(Exception):
    """A user-facing problem with a rows file or its state."""


class _FileDumper(BlockStyleDumper):
    """Block scalars for multi-line text; plain YAML values kept as written."""


def _represent_plain(dumper: yaml.SafeDumper, data: PlainScalar) -> yaml.ScalarNode:
    tag = dumper.resolve(yaml.ScalarNode, str(data), (True, False))
    return dumper.represent_scalar(tag, str(data), style=None)


_FileDumper.add_representer(PlainScalar, _represent_plain)
# The shared reader may hand back dict subclasses; write them as mappings.
_FileDumper.add_multi_representer(dict, yaml.SafeDumper.represent_dict)


def dump_yaml(document: Any) -> str:
    return yaml.dump(
        document, Dumper=_FileDumper, allow_unicode=True, sort_keys=False, width=4096
    )


def file_format(path: Path, fmt: str | None) -> str:
    if fmt is not None:
        if fmt not in FORMATS:
            raise RowFileError(f"invalid --format '{fmt}': expected one of {FORMATS}")
        return fmt
    return _EXTENSIONS.get(path.suffix.lower(), "yaml")


def _sorted_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _sorted_keys(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [_sorted_keys(item) for item in value]
    return value


def records_from_rows(
    rows: list[dict], columns: list[dict]
) -> tuple[list[dict], dict[str, int], dict[str, str]]:
    """Nested records plus each row's rev and base hash, keyed by its key value.

    Keys inside `json` values are written sorted: Postgres doesn't keep their
    original order, and sorted is at least stable and readable.
    """
    key = next(c["key"] for c in columns if c.get("is_key"))
    order = [key] + [c["key"] for c in columns if c["key"] != key]
    json_keys = {c["key"] for c in columns if c["type"] == "json"}
    active = set(order)
    records, revs, bases = [], {}, {}
    for row in rows:
        data = row["data"]
        flat = {
            k: _sorted_keys(data[k]) if k in json_keys else data[k]
            for k in order
            if data.get(k) is not None
        }
        records.append(nest(flat))
        revs[data[key]] = row["rev"]
        bases[data[key]] = row_hash(data, active)
    return records, revs, bases


def render_records(
    path: Path, fmt: str, records: list[dict], records_key: str | None
) -> str:
    """The text of the rows file, keeping an existing file's other top-level keys."""
    if fmt == "jsonl":
        if records_key:
            raise RowFileError("--records-key needs --format yaml or json")
        return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)

    document: Any = records
    if records_key is None and path.exists():
        # Keep the file's shape: the records key the reader finds in it.
        try:
            records_key = parse_records(path.name, path.read_bytes()).records_key
        except RecordsError:
            records_key = None
    if records_key:
        existing = _existing_document(path)
        if isinstance(existing, dict):
            document = dict(existing)
            document[records_key] = records
        else:
            document = {records_key: records}
    if fmt == "json":
        return json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    return dump_yaml(document)


def _existing_document(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return parse_document(path.name, path.read_bytes())
    except RecordsError as exc:
        raise RowFileError(f"can't read the existing {path}: {exc}") from None


# --- State ------------------------------------------------------------------


def state_path(rows_file: Path, dataset: str) -> Path:
    return rows_file.parent / ".groundline" / "rows" / dataset / f"{rows_file.name}.json"


def load_state(rows_file: Path, dataset: str, dataset_id: str) -> dict | None:
    """The state for this dataset and file, or None if it doesn't match."""
    path = state_path(rows_file, dataset)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if state.get("dataset_id") != dataset_id or state.get("file") != rows_file.name:
        return None
    return state


def save_state(
    rows_file: Path,
    dataset: str,
    dataset_id: str,
    key_column: str,
    revs: dict[str, int],
    bases: dict[str, str],
) -> None:
    path = state_path(rows_file, dataset)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "dataset_id": dataset_id,
        "key_column": key_column,
        "file": rows_file.name,
        "revs": revs,
        "bases": bases,
        "pulled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
