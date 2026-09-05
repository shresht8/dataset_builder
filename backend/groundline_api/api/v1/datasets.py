"""Dataset routes (§8).

    GET  /datasets
    POST /datasets
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/datasets", tags=["datasets"])

# TODO: list_datasets, create_dataset
