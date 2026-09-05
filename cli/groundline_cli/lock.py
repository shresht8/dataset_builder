"""groundline.lock read/write and hash verification (§7).

The lock file pins each dataset to a version and content_hash. `pull --lock`
restores exactly those, and CI verifies the hash before running.
"""

from __future__ import annotations

# TODO: read_lock(path) ; write_lock(path, entries) ; verify_hash(entry, snapshot)
