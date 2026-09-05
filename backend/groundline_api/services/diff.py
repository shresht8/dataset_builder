"""Version diffing (§6).

Reports rows added, removed, and modified (with per-field changes) between two
versions. Backs `GET /versions/diff` and `groundline datasets diff`.
"""

from __future__ import annotations

# TODO: diff_versions(from_v, to_v) -> {added, removed, modified}
