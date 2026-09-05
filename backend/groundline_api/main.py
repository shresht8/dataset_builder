"""FastAPI application factory and router wiring."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from groundline_api.api.v1.router import router as v1_router
from groundline_api.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # GL-1-8: optional first-admin bootstrap from the environment.
    if settings.bootstrap_admin_email:
        from groundline_api.bootstrap import ensure_admin
        from groundline_api.db import SessionLocal

        db = SessionLocal()
        try:
            ensure_admin(db, settings.bootstrap_admin_email)
        finally:
            db.close()
    yield


def create_app() -> FastAPI:
    # GL-1-9: passwordless dev login must never reach a shared deployment.
    if settings.auth_dev_login and settings.app_env != "dev":
        raise RuntimeError(
            "AUTH_DEV_LOGIN=true is only allowed when APP_ENV=dev "
            f"(got APP_ENV={settings.app_env!r}). Disable dev login or fix the profile."
        )
    app = FastAPI(title="Groundline Dataset Builder", lifespan=lifespan)
    app.include_router(v1_router, prefix="/v1")
    return app


app = create_app()
