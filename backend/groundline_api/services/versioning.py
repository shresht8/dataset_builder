"""Cutting a version (§6).

Steps: select rows (approved-only by default), serialise to JSONL in canonical
order, compute content_hash = sha256(snapshot), store snapshot + current schema
in object storage, assign the next integer version. Versions are immutable.
"""

from __future__ import annotations

# TODO: cut_version(dataset, notes, include_unapproved=False) -> DatasetVersion
