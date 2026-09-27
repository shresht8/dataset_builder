"""Version diffing (§6).

Reports rows added, removed, and modified (with per-field changes) between two
versions. Backs `GET /versions/diff` and `groundline datasets diff`.

Rows are matched by `id` (C2 line shape). Schema differences between the two
versions are tolerated: a field present in only one row's `data` reads as
`null` on the other side (C3).
"""

from __future__ import annotations

from typing import Any

_SCHEMA_COMPARE_FIELDS = ("type", "options", "required", "label")


def diff_versions(
    from_rows: list[dict[str, Any]],
    to_rows: list[dict[str, Any]],
    from_columns: list[dict[str, Any]],
    to_columns: list[dict[str, Any]],
) -> dict[str, Any]:
    from_by_id = {row["id"]: row for row in from_rows}
    to_by_id = {row["id"]: row for row in to_rows}

    added = [
        {"id": row["id"], "status": row["status"], "data": row["data"]}
        for row in to_rows
        if row["id"] not in from_by_id
    ]
    removed = [
        {"id": row["id"], "status": row["status"], "data": row["data"]}
        for row in from_rows
        if row["id"] not in to_by_id
    ]

    modified = []
    for row_id in sorted(set(from_by_id) & set(to_by_id)):
        old_row = from_by_id[row_id]
        new_row = to_by_id[row_id]

        status = None
        if old_row["status"] != new_row["status"]:
            status = {"old": old_row["status"], "new": new_row["status"]}

        changes = []
        for field in sorted(set(old_row["data"]) | set(new_row["data"])):
            old_value = old_row["data"].get(field)
            new_value = new_row["data"].get(field)
            if old_value != new_value:
                changes.append({"field": field, "old": old_value, "new": new_value})

        if status is not None or changes:
            modified.append({"id": row_id, "status": status, "changes": changes})

    return {
        "added": added,
        "removed": removed,
        "modified": modified,
        "schema_changes": _diff_schema(from_columns, to_columns),
    }


def _diff_schema(
    from_columns: list[dict[str, Any]], to_columns: list[dict[str, Any]]
) -> dict[str, list[str]]:
    from_by_key = {col["key"]: col for col in from_columns}
    to_by_key = {col["key"]: col for col in to_columns}

    added = sorted(set(to_by_key) - set(from_by_key))
    removed = sorted(set(from_by_key) - set(to_by_key))
    changed = sorted(
        key
        for key in set(from_by_key) & set(to_by_key)
        if any(
            from_by_key[key].get(field) != to_by_key[key].get(field)
            for field in _SCHEMA_COMPARE_FIELDS
        )
    )
    return {"added": added, "removed": removed, "changed": changed}
