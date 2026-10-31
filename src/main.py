"""FastAPI application factory — Modular Monolith entry point (ARCHITECTURE_LOCK D-013).

Run locally::

    uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

Swagger UI is served at ``/docs`` and ReDoc at ``/redoc``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.core import database
from src.core.config import Settings, get_settings
from src.core.http import build_http_client
from src.core.logging import configure_logging
from src.modules.auth import router as auth_router
from src.modules.content import router as content_router
from src.modules.content.domain.models import Channel
from src.modules.publishing.channels import get_registry
from src.modules.publishing.channels.router import router as channels_router
from src.modules.publishing.domain.publisher import Publisher
from src.modules.publishing.scheduler import (
    SCHEDULER_REQUIRES_ENCRYPTION_KEY,
    PostgresScheduler,
    PublisherResolver,
    scheduler_loop,
)
from src.modules.social_connect import router as social_connect_router
from src.modules.social_connect.infrastructure.status_marker import PostgresStatusMarker
from src.modules.tenancy import router as tenancy_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup / shutdown hooks.

    One shared ``httpx.AsyncClient`` (connection pool) lives for the app's lifetime and is
    injected into ``MetaGraphClient``. No database connection is opened eagerly — the
    async engine connects lazily on first request. On shutdown the engine's pool is
    disposed *only if it was ever created* (SPRINT-02.1 F6) so a DB-less boot stays DB-less.
    """
    settings: Settings = getattr(app.state, "settings", None) or get_settings()
    app.state.settings = settings
    app.state.http_client = build_http_client(settings)  # D-068: single outbound client
    app.state.scheduler_task = _start_scheduler(app, settings)  # SPRINT-07 B6 (None if off)
    try:
        yield
    finally:
        task: asyncio.Task[None] | None = app.state.scheduler_task
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await app.state.http_client.aclose()
        if database.get_engine.cache_info().currsize > 0:
            await database.get_engine().dispose()


def build_publisher_resolver(settings: Settings, http_client: object) -> PublisherResolver:
    """SPRINT-08 B-1: one ``Publisher`` per registry channel, built once from the channel
    definitions (Meta ⇒ ``MetaPublisher`` over the shared client; structural channels ⇒
    ``StructuralPublisher``) and resolved by the scheduler per ``job.channel``."""
    registry = get_registry()
    publishers: dict[Channel, Publisher] = {
        definition.channel: definition.build_publisher(settings, http_client)
        for definition in registry.all()
    }
    return publishers.__getitem__


def _start_scheduler(app: FastAPI, settings: Settings) -> asyncio.Task[None] | None:
    """B6: the publish worker runs in-process only when ``SCHEDULER_ENABLED=true``.

    SPRINT-08 A-1: a worker without ``TOKEN_ENCRYPTION_KEY`` cannot decrypt any page token and
    would silently fail every job with ``connection_unavailable`` — that is a configuration
    error, so boot is refused **before** anything is built (second guard in
    ``PostgresScheduler.__init__``).
    """
    if not settings.scheduler_enabled:
        return None
    if settings.token_encryption_key is None:
        raise RuntimeError(SCHEDULER_REQUIRES_ENCRYPTION_KEY)
    from src.core.security import get_token_encryption_service

    encryption = get_token_encryption_service(settings)
    scheduler = PostgresScheduler(
        database.get_session_factory,
        build_publisher_resolver(settings, app.state.http_client),
        PostgresStatusMarker(),
        settings,
        encryption=encryption,
    )
    logger.info("scheduler: started (poll=%ss)", settings.scheduler_poll_seconds)
    return asyncio.create_task(scheduler_loop(scheduler, settings.scheduler_poll_seconds))


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging()  # H2: httpx/httpcore at WARNING + root secret-redaction filter
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "AI Marketing Platform backend — Modular Monolith. "
            "Module 1: Social Connect (Meta Graph API for Facebook & Instagram, "
            "persisted in PostgreSQL). Module 2: Tenancy (tenants, brands, access grants). "
            "Module 3: Auth (Argon2id passwords, HS256 access JWT, rotating HttpOnly "
            "refresh cookie). Module 4: Content (drafts, revisions, owner-only human approval, "
            "scheduling — D-055). All non-auth routes require `Authorization: Bearer <JWT>`."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings

    api_v1 = APIRouter(prefix=settings.api_v1_prefix)
    api_v1.include_router(auth_router)
    api_v1.include_router(tenancy_router)
    api_v1.include_router(social_connect_router)
    api_v1.include_router(content_router)
    api_v1.include_router(channels_router)  # SPRINT-08 B-2: GET /channels[/{channel}]
    app.include_router(api_v1)

    @app.get("/health", tags=["System"], summary="Liveness probe")
    def health() -> dict[str, str]:
        return {"status": "ok", "app": settings.app_name, "env": settings.app_env}

    @app.get(
        "/health/ready",
        tags=["System"],
        summary="Readiness probe (database reachable)",
        responses={503: {"description": "Database unreachable"}},
    )
    async def health_ready() -> JSONResponse:
        # SPRINT-03 A6: a raw engine connection — no ORM session, no transaction, no commit.
        # F7: the error body never echoes the DSN or driver message.
        try:
            async with database.get_engine().connect() as conn:
                await conn.execute(text("SELECT 1"))
        except (SQLAlchemyError, OSError):
            return JSONResponse(
                status_code=503, content={"status": "not_ready", "database": "unreachable"}
            )
        return JSONResponse(status_code=200, content={"status": "ready", "database": "ok"})

    return app


app = create_app()
