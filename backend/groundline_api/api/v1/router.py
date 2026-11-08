"""Aggregates all v1 sub-routers under the /v1 prefix (§8)."""

from __future__ import annotations

from fastapi import APIRouter

from groundline_api.api.v1 import (
    auth,
    bundles,
    collections,
    datasets,
    health,
    imports,
    infer,
    rows,
    schema,
    store,
    sync,
    traces,
    users,
    versions,
)

router = APIRouter()
router.include_router(health.router)
router.include_router(auth.router)
router.include_router(users.router)
router.include_router(datasets.router)
router.include_router(collections.router)
router.include_router(store.router)
router.include_router(schema.router)
router.include_router(infer.router)
router.include_router(bundles.router)
router.include_router(rows.router)
router.include_router(versions.router)
router.include_router(imports.router)
router.include_router(sync.router)
router.include_router(traces.router)
