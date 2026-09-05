"""`dataset_rows` and `row_edits` tables (§3, §4).

    dataset_rows  id, dataset_id, data(jsonb), status, assignee,
                  source_trace_id?, rev, updated_by, updated_at
    row_edits     id, row_id, field, old_value, new_value, user_id, at

`rev` backs optimistic locking (§4 concurrency). `row_edits` is append-only.
Row status flows draft -> needs_review -> approved.
"""

from __future__ import annotations

from groundline_api.db import Base


class DatasetRow(Base):
    __tablename__ = "dataset_rows"
    # TODO: columns per §3


class RowEdit(Base):
    __tablename__ = "row_edits"
    # TODO: columns per §3 (append-only audit log)
