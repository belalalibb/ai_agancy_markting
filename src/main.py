"""FastAPI application factory — Modular Monolith entry point (ARCHITECTURE_LOCK D-013).

Run locally::

    uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

Swagger UI is served at ``/docs`` and ReDoc at ``/redoc``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from src.core.config import Settings, get_settings
from src.modules.social_connect import router as social_connect_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup / shutdown hooks (no external connections are opened eagerly)."""
    app.state.settings = get_settings()
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "AI Marketing Platform backend — Modular Monolith. "
            "Module 1: Social Connect (Meta Graph API for Facebook & Instagram)."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    api_v1 = APIRouter(prefix=settings.api_v1_prefix)
    api_v1.include_router(social_connect_router)
    app.include_router(api_v1)

    @app.get("/health", tags=["System"], summary="Liveness probe")
    def health() -> dict[str, str]:
        return {"status": "ok", "app": settings.app_name, "env": settings.app_env}

    return app


app = create_app()
