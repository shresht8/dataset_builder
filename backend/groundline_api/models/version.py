"""`dataset_versions` table (§3, §6).

    dataset_versions  dataset_id, version, snapshot_uri, content_hash,
                      schema_snapshot(jsonb), row_count, notes,
                      created_by, created_at

Each version embeds its own schema snapshot (§2.2) so an export is
self-describing. Versions are immutable.
"""

from __future__ import annotations

from groundline_api.db import Base


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"
    # TODO: columns per §3
