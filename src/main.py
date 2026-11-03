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
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import database
from src.core.config import Settings, get_settings
from src.core.database import get_db_session
from src.core.http import build_http_client
from src.core.logging import configure_logging
from src.modules.auth import router as auth_router
from src.modules.brand_brain.playbooks import router as playbooks_router
from src.modules.brand_brain.router import router as brand_brain_router
from src.modules.brand_brain.share import public_router as diagnosis_public_router
from src.modules.brand_brain.share import router as diagnosis_share_router
from src.modules.content import router as content_router
from src.modules.content.domain.models import Channel
from src.modules.entitlements.dependencies import counter_guard_dependency
from src.modules.entitlements.router import admin_router as entitlements_admin_router
from src.modules.entitlements.router import router as entitlements_router
from src.modules.ops.guards import (
    feature_flag,
    platform_readonly_guard,
    require_brand_not_suspended,
)
from src.modules.ops.router import router as ops_admin_router
from src.modules.publishing.channels import get_registry
from src.modules.publishing.channels.router import router as channels_router
from src.modules.publishing.domain.publisher import Publisher
from src.modules.publishing.scheduler import (
    SCHEDULER_REQUIRES_ENCRYPTION_KEY,
    PostgresScheduler,
    PublisherResolver,
    SchedulerStep,
    scheduler_loop,
)
from src.modules.social_connect import router as social_connect_router
from src.modules.social_connect.infrastructure.status_marker import PostgresStatusMarker
from src.modules.tenancy import router as tenancy_router
from src.modules.whatsapp.router import router as whatsapp_router
from src.modules.whatsapp.webhook import router as whatsapp_webhook_router
from src.ui.router import STATIC_DIR as UI_STATIC_DIR
from src.ui.router import VendorCacheControl
from src.ui.router import router as ui_router

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
    return asyncio.create_task(
        scheduler_loop(
            scheduler,
            settings.scheduler_poll_seconds,
            extra_steps=tuple(
                step
                for step in (
                    settle_stale_cost_reservations_step(settings),
                    pulse_scheduler_step(settings),
                )
                if step is not None
            ),
        )
    )


def settle_stale_cost_reservations_step(settings: Settings) -> SchedulerStep:
    """SPRINT-11 B-4 (D-075): second scheduler step — ``pending`` ``ai_cost_log`` rows older
    than ``2 × AI_HTTP_TIMEOUT_SECONDS`` become ``ambiguous`` (counted against the cap)."""
    from src.modules.ai_gateway.budget_guard import settle_stale_reservations
    from src.modules.ai_gateway.infrastructure.pg_cost_log import PgCostLogWriter

    writer = PgCostLogWriter()

    async def step(now: datetime) -> int:
        return await settle_stale_reservations(
            writer, now=now, timeout_seconds=settings.ai_http_timeout_seconds
        )

    return step


def pulse_scheduler_step(settings: Settings) -> SchedulerStep | None:
    """SPRINT-12 B-3: third scheduler step — ``Pulse.run(now)`` every
    ``PULSE_INTERVAL_SECONDS``. ``FEATURE_PULSE=false`` ⇒ ``None`` (nothing scheduled).
    Check (6) reads plan counters through the entitlements adapter bound here (D-016);
    removing ``pulse`` = delete this function and the two mounts in ``create_app``."""
    if not settings.feature_pulse:
        return None
    from src.modules.entitlements.dependencies import PgPlanCounterUsageReader
    from src.modules.pulse.scheduler_step import pulse_step

    return pulse_step(
        interval_seconds=settings.pulse_interval_seconds,
        counters_factory=lambda session: PgPlanCounterUsageReader(session),
    )


def _published_post_lookup(session: Annotated[AsyncSession, Depends(get_db_session)]) -> object:
    """SPRINT-12 B-2: module-level so FastAPI can resolve the (postponed) annotation; the
    publishing import stays local (removability)."""
    from src.modules.publishing.infrastructure.postgres_repository import PgPublishedPostLookup

    return PgPublishedPostLookup(session)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging()  # H2: httpx/httpcore at WARNING + root secret-redaction filter
    if not settings.feature_budget_guard_strict:  # SPRINT-11 B-4 kill switch (D-076)
        logger.warning(
            "FEATURE_BUDGET_GUARD_STRICT=false — unpriced models are allowed on the platform "
            "key (cost recorded as NULL); re-enable before production."
        )
    if not settings.feature_entitlements_enforced:  # SPRINT-11 C-1 kill switch (D-076)
        logger.warning(
            "FEATURE_ENTITLEMENTS_ENFORCED=false — plan limits (D-073) are not enforced; "
            "every brand is allowed. Re-enable before production."
        )
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

    # SPRINT-13 A-2: ``platform_readonly`` (DB flag, Settings fallback) ⇒ 503 on every write
    # under /api/v1 except /auth/* and /admin/*; ``brands.suspended_at`` ⇒ 403 on brand writes.
    # Both are router-level dependencies — no handler changed. Removing ``ops`` = delete the
    # two imports above, ``suspended`` below and the ``ops_admin_router`` mount.
    api_v1 = APIRouter(
        prefix=settings.api_v1_prefix, dependencies=[Depends(platform_readonly_guard)]
    )
    suspended = [Depends(require_brand_not_suspended)]
    api_v1.include_router(auth_router)
    api_v1.include_router(tenancy_router, dependencies=suspended)
    api_v1.include_router(social_connect_router, dependencies=suspended)
    api_v1.include_router(content_router, dependencies=suspended)
    api_v1.include_router(channels_router)  # SPRINT-08 B-2: GET /channels[/{channel}]
    # SPRINT-09 B-1: public, signed Cloud API webhook (GET handshake + POST events).
    api_v1.include_router(whatsapp_webhook_router)
    api_v1.include_router(whatsapp_router, dependencies=suspended)  # SPRINT-09 B-2/B-3
    api_v1.include_router(brand_brain_router, dependencies=suspended)  # SPRINT-10 B-1
    api_v1.include_router(playbooks_router)  # SPRINT-12.1-B D-4: GET /playbooks (CurrentUser)
    # SPRINT-12 C-1: share/unshare (OWNER) + the public, watermarked ``GET /d/{token}``.
    api_v1.include_router(diagnosis_share_router, dependencies=suspended)
    api_v1.include_router(diagnosis_public_router)
    api_v1.include_router(ops_admin_router)  # SPRINT-13 A: /admin/{actions,brands,flags,…}
    # SPRINT-11 C-5 (D-073/D-076): removable — see src/modules/entitlements/REMOVABILITY.md
    api_v1.include_router(entitlements_router)
    api_v1.include_router(entitlements_admin_router)
    # SPRINT-11.1 C-1 (D-077/D-076): /admin/ai/* — provider catalog control. Local import so
    # deleting ``ai_gateway/catalog/`` + this block removes the feature; the kill switch
    # ``FEATURE_PROVIDER_CATALOG_ENABLED`` only governs the *runtime* resolver, the admin
    # routes stay available for seeding — see ai_gateway/catalog/REMOVABILITY.md.
    from src.modules.ai_gateway.catalog.router import router as admin_ai_router

    api_v1.include_router(admin_ai_router)
    # SPRINT-11.1 A-2 (E2): bind the consumer-owned ``CounterGuard`` ports of ``content`` and
    # ``whatsapp`` to the entitlements adapter. Removing ``entitlements`` = delete these two
    # lines; the ports then yield ``None`` (no quota check) — see entitlements/REMOVABILITY.md.
    from src.modules.content import entitlements_port as content_port
    from src.modules.whatsapp import entitlements_port as whatsapp_port

    app.dependency_overrides[content_port.get_counter_guard] = counter_guard_dependency(
        content_port.CounterQuotaExceededError, content_port.CounterPlanInactiveError
    )
    app.dependency_overrides[whatsapp_port.get_counter_guard] = counter_guard_dependency(
        whatsapp_port.CounterQuotaExceededError, whatsapp_port.CounterPlanInactiveError
    )
    # SPRINT-12 B-2 (D-079/D-016): bind ``whatsapp``'s consumer-owned ``PublishedPostLookup``
    # to the publishing adapter. Removing ``publishing`` = delete these lines; the port then
    # yields ``None`` (no attribution). The admin wa-caps route lives on the whatsapp admin
    # router (D-082) — see whatsapp/REMOVABILITY.md.
    from src.modules.whatsapp import lead_loop as whatsapp_lead_loop
    from src.modules.whatsapp.router import admin_router as whatsapp_admin_router

    app.dependency_overrides[whatsapp_lead_loop.get_published_post_lookup] = _published_post_lookup
    # SPRINT-13 A-2: the two feature switches read the DB flag first (``platform_flags``),
    # ``Settings`` next — flipping the row turns the feature off without a redeploy.
    app.dependency_overrides[whatsapp_lead_loop.get_lead_loop_enabled] = feature_flag(
        "feature_lead_loop"
    )
    api_v1.include_router(whatsapp_admin_router)
    if settings.feature_pulse:
        from src.modules.pulse import router as pulse_module

        app.dependency_overrides[pulse_module.get_pulse_enabled] = feature_flag("feature_pulse")
        api_v1.include_router(pulse_module.router, dependencies=suspended)
    else:
        logger.warning("FEATURE_PULSE=false — Pulse routes and scheduler step are not mounted")
    app.include_router(api_v1)
    # SPRINT-12.1 B (D-076): L1 operator UI under /app — server-rendered, talks to the API
    # in-process only. Removing ``src/ui`` = delete the two imports above + these two lines
    # (see src/ui/REMOVABILITY.md).
    app.include_router(ui_router)
    app.mount(
        "/app/static",
        VendorCacheControl(StaticFiles(directory=str(UI_STATIC_DIR))),
        name="ui-static",
    )

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
