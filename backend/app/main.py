"""FastAPI application factory.

Everything that reads customer data goes through a tenant context
(app.core.auth.tenant_session → app.core.rls) — there is no un-scoped data route.
"""

from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy import text

from app import agents
from app.api import ai as ai_api
from app.api import auth as auth_api
from app.api import contracts as contracts_api
from app.api import dashboard as dashboard_api
from app.api import documents as documents_api
from app.api import obligations as obligations_api
from app.api import risks as risks_api
from app.core.config import settings
from app.core.db import engine


def create_app() -> FastAPI:
    app = FastAPI(
        title="Obliance",
        version="0.1.0",
        docs_url="/api/docs" if settings.app_env != "prod" else None,
        openapi_url="/api/openapi.json",
    )
    app.include_router(auth_api.router)
    app.include_router(contracts_api.router)
    app.include_router(documents_api.router)
    app.include_router(obligations_api.router)
    app.include_router(risks_api.router)
    app.include_router(dashboard_api.router)
    app.include_router(ai_api.router)
    agents.register()  # ADR-0006 §3 listeners: expire suggestions, run intake

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
