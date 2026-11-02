"""groundline.lock read/write and hash verification (§7).

The lock file pins each dataset to a version and content_hash, plus the
format and shape it was pulled in when those aren't the defaults (GL-3.5-13)::

    {"datasets": {"<name>": {"version": N, "content_hash": "sha256:...",
                             "format": "yaml", "shape": "nested"}}}

A missing `format` / `shape` means `jsonl` / `flat`, so lock files written
before GL-3.5-13 (and default pulls) are unchanged.

`pull <name>@vN` updates one entry (creating the file if absent).
`pull --lock` restores every pinned dataset in its pinned format and shape,
verifying each download's content_hash against the pin and failing loudly on
mismatch (CI use case). A `--format`/`--shape` flag that contradicts a pinned
value is an error rather than a silent switch; for entries without a pinned
value the flag applies, as before.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from groundline_schema.render import FORMATS, SHAPES

from groundline_cli.client import ApiClient
from groundline_cli.pull import pull_version

_DEFAULTS = {"format": "jsonl", "shape": "flat"}
_ALLOWED = {"format": FORMATS, "shape": SHAPES}

DEFAULT_LOCK_PATH = Path("groundline.lock")


class LockError(Exception):
    """A user-facing CLI error: print the message and exit non-zero."""


def _load(path: Path) -> dict[str, dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LockError(f"malformed lock file {path}: {exc}") from exc
    datasets = data.get("datasets") if isinstance(data, dict) else None
    if not isinstance(datasets, dict):
        raise LockError(f"malformed lock file {path}: expected a 'datasets' object")
    return datasets


def read_lock(path: Path) -> dict[str, dict]:
    """Read pinned datasets for `pull --lock`.

    Raises `LockError` if the file is missing, malformed, or has no pins.
    """
    if not path.exists():
        raise LockError(f"lock file not found: {path}")
    datasets = _load(path)
    if not datasets:
        raise LockError(f"lock file {path} has no pinned datasets")
    for name, entry in datasets.items():
        if not isinstance(entry, dict) or "version" not in entry or "content_hash" not in entry:
            raise LockError(f"malformed lock entry for '{name}' in {path}")
        for field, allowed in _ALLOWED.items():
            if field in entry and entry[field] not in allowed:
                raise LockError(f"malformed lock entry for '{name}' in {path}: bad {field}")
    return datasets


def write_lock(path: Path, datasets: dict[str, dict]) -> None:
    """Write the lock deterministically: sorted dataset keys, stable indent, trailing newline."""
    payload = {"datasets": {name: datasets[name] for name in sorted(datasets)}}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def update_lock(
    path: Path,
    name: str,
    version: int,
    content_hash: str,
    fmt: str = "jsonl",
    shape: str = "flat",
) -> None:
    """Add or replace one dataset's pin, creating the lock file if absent."""
    datasets = _load(path)
    entry: dict = {"version": version, "content_hash": content_hash}
    for field, value in (("format", fmt), ("shape", shape)):
        if value != _DEFAULTS[field]:
            entry[field] = value
    datasets[name] = entry
    write_lock(path, datasets)


def _resolve(name: str, entry: dict, field: str, flag: str | None) -> str:
    """The pinned value, else the flag, else the default; a contradicting flag fails."""
    pinned = entry.get(field)
    if flag is not None and pinned is not None and flag != pinned:
        raise LockError(
            f"{name} is pinned as {field} '{pinned}' in the lock file, but --{field} "
            f"'{flag}' was given; drop the flag, or re-pin with "
            f"`pull {name}@v{entry['version']} --{field} {flag}`"
        )
    return pinned or flag or _DEFAULTS[field]


def restore_lock(
    client: ApiClient,
    lock_path: Path,
    fmt: str | None,
    out: Path,
    shape: str | None = None,
) -> list[tuple[str, int, str]]:
    """Pull every dataset pinned in the lock, verifying each against its pin.

    Each dataset is written in its pinned format and shape. Returns
    `(name, version, content_hash)` for each restored dataset. On the first
    pin mismatch, removes that dataset's just-written version directory and
    raises `LockError` naming the dataset/version/expected/actual hash.
    """
    entries = read_lock(lock_path)
    # Check every entry against the flags before writing anything.
    plan = {
        name: (_resolve(name, entry, "format", fmt), _resolve(name, entry, "shape", shape))
        for name, entry in entries.items()
    }
    restored = []
    for name in sorted(entries):
        entry = entries[name]
        version = entry["version"]
        pinned_hash = entry["content_hash"]
        entry_fmt, entry_shape = plan[name]
        content_hash = pull_version(client, name, version, entry_fmt, out, entry_shape)
        if content_hash != pinned_hash:
            shutil.rmtree(out / name, ignore_errors=True)
            raise LockError(
                f"lock verification failed for {name}@v{version}: "
                f"expected {pinned_hash}, got {content_hash}"
            )
        restored.append((name, version, content_hash))
    return restored
