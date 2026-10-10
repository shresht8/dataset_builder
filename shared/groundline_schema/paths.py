"""Dotted column paths: flatten nested records, nest flat rows (GL-3.5-13).

Rows are stored flat: `data` keys are column keys, and a dotted key such as
`expected.golden_response` stands for a nested path. Import and sync flatten
source records onto those keys; exports and `rows pull` can nest them back.

- A literal dotted key (`{"a.b": 1}`) and the nested form (`{"a": {"b": 1}}`)
  are the same path; only two sources producing one path is an error.
- `leaves` are the dataset's `json` column keys: flattening never recurses into
  them, so an object-valued `json` column round-trips intact.
- Arrays, scalars and `{}` are always leaves.

`flatten(nest(row), leaves) == row` for every valid flat row.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any


class PathError(ValueError):
    """A key is not a valid dotted path, or two sources produce the same path."""


def split_path(path: str) -> list[str]:
    """Split a dotted path into segments; every segment must be non-empty."""
    segments = path.split(".")
    if any(segment == "" for segment in segments):
        raise PathError(f"invalid path '{path}': empty segment")
    return segments


def flatten(obj: dict[str, Any], leaves: Collection[str] = frozenset()) -> dict[str, Any]:
    """Flatten a nested record to `{dotted path: leaf value}`."""
    flat: dict[str, Any] = {}

    def walk(node: dict[str, Any], prefix: str) -> None:
        for key, value in node.items():
            split_path(key)
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict) and value and path not in leaves:
                walk(value, path)
                continue
            if path in flat:
                raise PathError(f"path '{path}' appears twice")
            flat[path] = value

    walk(obj, "")
    return flat


def nest(flat: dict[str, Any]) -> dict[str, Any]:
    """Inverse of `flatten`: build nested objects from dotted keys.

    An intermediate object exists only if at least one of its children is
    present. A path that is both a value and a parent of another path is an
    error (the schema forbids such column keys, so stored rows never hit it).
    """
    nested: dict[str, Any] = {}
    created: set[int] = {id(nested)}  # objects built here, as opposed to leaf values
    for path, value in flat.items():
        segments = split_path(path)
        node = nested
        for segment in segments[:-1]:
            child = node.get(segment)
            if child is None and segment not in node:
                child = node[segment] = {}
                created.add(id(child))
            elif id(child) not in created:
                raise PathError(f"path '{path}' clashes with a value at '{segment}'")
            node = child
        last = segments[-1]
        if last in node:
            raise PathError(f"path '{path}' clashes with another path")
        node[last] = value
    return nested
