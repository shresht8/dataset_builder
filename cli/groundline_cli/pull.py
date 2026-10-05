"""Pull a dataset version's snapshot to disk, with hash verification (§7).

`pull_version` is the shared core for `groundline pull <name>@vN` and, from
GL-3-8, `groundline pull --lock`. It always fetches the `jsonl` bytes first
and verifies `content_hash` (C3) before writing anything to disk, even when
the requested format is json/yaml — so a hash mismatch never leaves a
half-written or unverified data file behind.

Output layout, chosen for this card::

    <out>/<name>/v<N>/rows.<fmt>
    <out>/<name>/v<N>/manifest.json
    <out>/<name>/v<N>/<name>.schema.json     # JSON Schema sidecar
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from groundline_cli.client import ApiClient, ApiError

TARGET_PATTERN = re.compile(r"^(?P<name>[a-z0-9][a-z0-9_-]{0,99})@v(?P<version>\d+)$")


class HashMismatchError(Exception):
    """The downloaded jsonl bytes don't match the version's content_hash."""


def parse_target(target: str) -> tuple[str, int]:
    """Parse `name@vN`. Raises `ApiError` (a clean, one-line CLI error) on bad syntax."""
    match = TARGET_PATTERN.match(target)
    if not match:
        raise ApiError(f"invalid target '{target}': expected name@vN")
    return match.group("name"), int(match.group("version"))


def pull_version(client: ApiClient, name: str, version: int, fmt: str, out: Path) -> str:
    """Fetch, verify, and write one dataset version. Returns the verified content_hash."""
    dataset_id = client.resolve_dataset_id(name)

    jsonl_bytes = client.get_version_bytes(dataset_id, version, "jsonl")
    manifest = client.get_manifest(dataset_id, version)
    expected_hash = manifest["content_hash"]
    actual_hash = "sha256:" + hashlib.sha256(jsonl_bytes).hexdigest()
    if actual_hash != expected_hash:
        raise HashMismatchError(
            f"content hash mismatch for {name}@v{version}: "
            f"expected {expected_hash}, got {actual_hash}"
        )

    data_bytes = (
        jsonl_bytes if fmt == "jsonl" else client.get_version_bytes(dataset_id, version, fmt)
    )
    sidecar = client.get_jsonschema(dataset_id, version)

    version_dir = out / name / f"v{version}"
    version_dir.mkdir(parents=True, exist_ok=True)
    (version_dir / f"rows.{fmt}").write_bytes(data_bytes)
    (version_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    (version_dir / f"{name}.schema.json").write_text(
        json.dumps(sidecar, indent=2), encoding="utf-8"
    )
    return expected_hash


def check_drift(client: ApiClient, name: str, version: int) -> list[str]:
    """Required columns in the dataset's *current* schema missing from the pulled
    version's schema (Q2). Returns the sorted list of missing column keys."""
    dataset_id = client.resolve_dataset_id(name)
    sidecar = client.get_jsonschema(dataset_id, version)
    pulled_required = set(sidecar.get("properties", {}).get("data", {}).get("required", []))
    current_schema = client.get_schema(dataset_id)
    current_required = {c["key"] for c in current_schema["columns"] if c.get("required")}
    return sorted(current_required - pulled_required)
