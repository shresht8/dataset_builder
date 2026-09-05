"""FastAPI application factory and router wiring."""

from __future__ import annotations

from fastapi import FastAPI

from groundline_api.api.v1.router import router as v1_router


def create_app() -> FastAPI:
    app = FastAPI(title="Groundline Dataset Builder")
    app.include_router(v1_router, prefix="/v1")
    return app


app = create_app()
