"""Object storage for version snapshots (§6).

Writes datasets/{name}/v{n}/{manifest.json,schema.json,rows.jsonl} to an
S3-compatible bucket and reads them back for export/pull.
"""

from __future__ import annotations

# TODO: put_snapshot(dataset, version, files) ; get_object(uri) -> bytes
