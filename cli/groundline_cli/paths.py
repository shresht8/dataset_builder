"""Dataset paths in the CLI (Phase 4, C1).

A dataset is addressed by `collection/[folder/…]/dataset`, or by its bare name
when it is unfiled. Every segment matches the dataset name pattern, so a path
is also a safe relative directory path.
"""

from __future__ import annotations

import re

from groundline_cli.client import ApiClient, ApiError

SEGMENT = r"[a-z0-9][a-z0-9_-]{0,99}"
SEGMENT_RE = re.compile(rf"^{SEGMENT}$")


def split_path(path: str) -> list[str]:
    segments = path.split("/")
    for segment in segments:
        if not SEGMENT_RE.match(segment):
            raise ApiError(f"invalid path '{path}': bad segment '{segment}'")
    return segments


def folder_paths(tree: dict) -> dict[str, str]:
    """Folder id -> collection-relative folder path, from a collection tree."""
    by_id = {f["id"]: f for f in tree["folders"]}

    def path(folder_id: str) -> str:
        parts = []
        while folder_id:
            parts.append(by_id[folder_id]["name"])
            folder_id = by_id[folder_id]["parent_id"]
        return "/".join(reversed(parts))

    return {folder_id: path(folder_id) for folder_id in by_id}


def dataset_paths(tree: dict) -> dict[str, dict]:
    """Collection-relative dataset path -> tree dataset entry."""
    folders = folder_paths(tree)
    out = {}
    for dataset in tree["datasets"]:
        folder = folders.get(dataset["folder_id"]) if dataset["folder_id"] else None
        out[f"{folder}/{dataset['name']}" if folder else dataset["name"]] = dataset
    return out


def locate(client: ApiClient, segments: list[str]) -> tuple[dict, str | None]:
    """`[collection, folder, …]` -> (collection, folder id or None for its root)."""
    collection = client.find_collection(segments[0])
    if len(segments) == 1:
        return collection, None
    tree = client.get_collection(collection["id"])
    wanted = "/".join(segments[1:])
    for folder_id, path in folder_paths(tree).items():
        if path == wanted:
            return collection, folder_id
    raise ApiError(f"no folder '{wanted}' in collection '{segments[0]}'")
