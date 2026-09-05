"""`datasets` and `dataset_columns` tables (§3).

    datasets         id, project_id, name, description, feature_id?, created_by
    dataset_columns  dataset_id, key, label, type, options, required, order, archived
"""

from __future__ import annotations

from groundline_api.db import Base


class Dataset(Base):
    __tablename__ = "datasets"
    # TODO: columns per §3


class DatasetColumn(Base):
    __tablename__ = "dataset_columns"
    # TODO: columns per §3; `type` uses groundline_schema.ColumnType
