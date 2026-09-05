"""Aggregates all v1 sub-routers under the /v1 prefix (§8)."""

from __future__ import annotations

from fastapi import APIRouter

from groundline_api.api.v1 import (
    auth,
    datasets,
    imports,
    rows,
    schema,
    traces,
    versions,
)

router = APIRouter()
router.include_router(auth.router)
router.include_router(datasets.router)
router.include_router(schema.router)
router.include_router(rows.router)
router.include_router(versions.router)
router.include_router(imports.router)
router.include_router(traces.router)
