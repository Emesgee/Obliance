"""FastAPI application factory.

Only the health endpoints exist yet. Everything that reads customer data goes
through a tenant context (app.core.rls) — there is no un-scoped data route.
"""

from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy import text

from app.core.config import settings
from app.core.db import engine


def create_app() -> FastAPI:
    app = FastAPI(
        title="Obliance",
        version="0.1.0",
        docs_url="/api/docs" if settings.app_env != "prod" else None,
        openapi_url="/api/openapi.json",
    )

    @app.get("/api/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok", "env": settings.app_env}

    @app.get("/api/health/db", tags=["health"])
    def health_db() -> dict[str, str]:
        # No tenant context on purpose: SELECT 1 touches no customer table.
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"database": "ok"}

    return app


app = create_app()
