"""groundline.lock read/write and hash verification (§7).

The lock file pins each dataset to a version and content_hash::

    {"datasets": {"<name>": {"version": N, "content_hash": "sha256:..."}}}

`pull <name>@vN` updates one entry (creating the file if absent).
`pull --lock` restores every pinned dataset, verifying each download's
content_hash against the pin and failing loudly on mismatch (CI use case).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from groundline_cli.client import ApiClient
from groundline_cli.pull import pull_version

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
    return datasets


def write_lock(path: Path, datasets: dict[str, dict]) -> None:
    """Write the lock deterministically: sorted dataset keys, stable indent, trailing newline."""
    payload = {"datasets": {name: datasets[name] for name in sorted(datasets)}}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def update_lock(path: Path, name: str, version: int, content_hash: str) -> None:
    """Add or replace one dataset's pin, creating the lock file if absent."""
    datasets = _load(path)
    datasets[name] = {"version": version, "content_hash": content_hash}
    write_lock(path, datasets)


def restore_lock(
    client: ApiClient, lock_path: Path, fmt: str, out: Path
) -> list[tuple[str, int, str]]:
    """Pull every dataset pinned in the lock, verifying each against its pin.

    Returns `(name, version, content_hash)` for each restored dataset. On the
    first pin mismatch, removes that dataset's just-written version directory
    and raises `LockError` naming the dataset/version/expected/actual hash.
    """
    entries = read_lock(lock_path)
    restored = []
    for name in sorted(entries):
        entry = entries[name]
        version = entry["version"]
        pinned_hash = entry["content_hash"]
        content_hash = pull_version(client, name, version, fmt, out)
        if content_hash != pinned_hash:
            shutil.rmtree(out / name, ignore_errors=True)
            raise LockError(
                f"lock verification failed for {name}@v{version}: "
                f"expected {pinned_hash}, got {content_hash}"
            )
        restored.append((name, version, content_hash))
    return restored
