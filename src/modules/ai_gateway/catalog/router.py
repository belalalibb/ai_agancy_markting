"""``/api/v1/admin/ai/*`` — provider catalog control (SPRINT-11.1 C-1/C-2, D-077).

Eleven protected routes, all ``PlatformAdmin`` (``PLATFORM_ADMIN_USER_IDS``; empty list ⇒ 403
for everyone), no public route:

| #  | Method | Path                              | Note                                          |
|----|--------|-----------------------------------|-----------------------------------------------|
| 1  | GET    | /admin/ai/providers               | ``has_credential`` only — never the key       |
| 2  | POST   | /admin/ai/providers               | ``api_key`` encrypted on receipt ⇒ 201        |
| 3  | PATCH  | /admin/ai/providers/{provider_id} | new key replaces; explicit ``null`` clears    |
| 4  | DELETE | /admin/ai/providers/{provider_id} | 204; CASCADE to models + routes               |
| 5  | GET    | /admin/ai/models                  | ``?provider_id=``                             |
| 6  | POST   | /admin/ai/models                  | 201                                           |
| 7  | PATCH  | /admin/ai/models/{model_id}       |                                               |
| 8  | DELETE | /admin/ai/models/{model_id}       | 204                                           |
| 9  | GET    | /admin/ai/routing                 | ``?purpose=`` ⇒ ordered by priority           |
| 10 | PUT    | /admin/ai/routing/{purpose}       | replaces the whole chain atomically           |
| 11 | POST   | /admin/ai/probe                   | 5/min per admin; provider fail ⇒ 200 ok=false |

Errors are HTTP-literal (B-6, no new ``ErrorCategory``): ``catalog_not_found`` 404 ·
``catalog_conflict`` 409 · ``provider_host_not_allowed`` 422 · ``rate_limited`` 429 ·
``BudgetGuardError`` through ``budget_error_to_http`` (``ai_model_unpriced`` 503, platform cap).

Removable with the module (D-076): ``src/main.py`` mounts this router behind a local import —
see ``catalog/REMOVABILITY.md``.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.core.database import get_db_session
from src.core.security import get_token_encryption_service
from src.modules.ai_gateway.budget_guard import BudgetGuardError
from src.modules.ai_gateway.catalog.admin_service import CatalogAdminService
from src.modules.ai_gateway.catalog.domain import PURPOSES, CatalogSnapshot
from src.modules.ai_gateway.catalog.infrastructure.pg_repository import (
    CATALOG_CACHE,
    PgCatalogRepository,
)
from src.modules.ai_gateway.catalog.schemas import (
    ModelCreate,
    ModelOut,
    ModelPatch,
    ProbeOut,
    ProbeRequest,
    ProviderCreate,
    ProviderOut,
    ProviderPatch,
    RouteOut,
    RoutingPut,
)
from src.modules.ai_gateway.catalog.service import CatalogError, CatalogPriceTable
from src.modules.ai_gateway.dependencies import (
    BudgetReader,
    CostWriter,
    HostResolver,
    build_budget_guard,
)
from src.modules.ai_gateway.gateway import AIGateway
from src.modules.ai_gateway.http_errors import budget_error_to_http
from src.modules.auth.dependencies import PlatformAdmin, get_rate_limiter
from src.modules.auth.domain.rate_limit import RateLimiter
from src.modules.ops.audit import Audit  # SPRINT-13 A-1: one admin_actions row per write

router = APIRouter(prefix="/admin/ai", tags=["Platform admin — AI catalog (D-077)"])

PROBE_BUCKET = "admin:ai-probe"
PROBE_LIMIT = 5
PROBE_WINDOW_SECONDS = 60


# ---------------------------------------------------------------- wiring --
def get_catalog_admin_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    resolver: HostResolver = None,
) -> CatalogAdminService:
    """Request-scoped admin service over the PG repository; every write invalidates the
    process-wide snapshot cache so the next guarded call sees the change. SPRINT-12 A-4:
    ``resolver`` (``get_host_resolver``) is the DNS lookup behind the host guard."""
    encryption = get_token_encryption_service(settings) if settings.token_encryption_key else None
    return CatalogAdminService(
        PgCatalogRepository(session),
        encryption=encryption,
        allow_local_hosts=settings.ai_allow_local_provider_hosts,
        invalidate=CATALOG_CACHE.invalidate,
        resolver=resolver,
    )


AdminService = Annotated[CatalogAdminService, Depends(get_catalog_admin_service)]
Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]


def to_http(exc: CatalogError) -> HTTPException:
    return HTTPException(exc.status, detail={"error": exc.error, "message": exc.message})


def _purpose_or_422(purpose: str) -> str:
    if purpose not in PURPOSES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error": "catalog_unknown_purpose",
                "message": f"purpose must be one of {', '.join(PURPOSES)}",
            },
        )
    return purpose


# ------------------------------------------------------------- providers --
@router.get("/providers", response_model=list[ProviderOut], summary="List AI providers")
async def list_providers(_: PlatformAdmin, service: AdminService) -> list[ProviderOut]:
    return [ProviderOut.from_domain(p) for p in await service.list_providers()]


@router.post(
    "/providers",
    response_model=ProviderOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a provider (api_key encrypted on receipt, never returned)",
)
async def create_provider(
    payload: ProviderCreate, actor: PlatformAdmin, service: AdminService, audit: Audit
) -> ProviderOut:
    try:
        created = await service.create_provider(payload, actor_user_id=actor.user_id)
    except CatalogError as exc:
        raise to_http(exc) from exc
    await audit.record(
        actor_user_id=actor.user_id,
        action="provider_created",
        target_kind="ai_provider",
        target_id=str(created.id),
        after={"slug": created.slug, "kind": str(created.kind)},
    )
    return ProviderOut.from_domain(created)


@router.patch("/providers/{provider_id}", response_model=ProviderOut, summary="Update a provider")
async def patch_provider(
    provider_id: UUID,
    payload: ProviderPatch,
    actor: PlatformAdmin,
    service: AdminService,
    audit: Audit,
) -> ProviderOut:
    try:
        updated = await service.patch_provider(provider_id, payload, actor_user_id=actor.user_id)
    except CatalogError as exc:
        raise to_http(exc) from exc
    await audit.record(
        actor_user_id=actor.user_id,
        action="provider_updated",
        target_kind="ai_provider",
        target_id=str(provider_id),
        after={"fields": sorted(payload.model_dump(exclude_unset=True))},
    )
    return ProviderOut.from_domain(updated)


@router.delete(
    "/providers/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a provider (CASCADE to its models and routes)",
)
async def delete_provider(
    provider_id: UUID, actor: PlatformAdmin, service: AdminService, audit: Audit
) -> Response:
    try:
        await service.delete_provider(provider_id, actor_user_id=actor.user_id)
    except CatalogError as exc:
        raise to_http(exc) from exc
    await audit.record(
        actor_user_id=actor.user_id,
        action="provider_deleted",
        target_kind="ai_provider",
        target_id=str(provider_id),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- models --
@router.get("/models", response_model=list[ModelOut], summary="List AI models")
async def list_models(
    _: PlatformAdmin, service: AdminService, provider_id: UUID | None = None
) -> list[ModelOut]:
    return [ModelOut.from_domain(m) for m in await service.list_models(provider_id)]


@router.post(
    "/models",
    response_model=ModelOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a model with its pricing",
)
async def create_model(
    payload: ModelCreate, actor: PlatformAdmin, service: AdminService, audit: Audit
) -> ModelOut:
    try:
        created = await service.create_model(payload, actor_user_id=actor.user_id)
    except CatalogError as exc:
        raise to_http(exc) from exc
    await audit.record(
        actor_user_id=actor.user_id,
        action="model_created",
        target_kind="ai_model",
        target_id=str(created.id),
        after={"provider_id": str(created.provider_id), "model_name": created.model_name},
    )
    return ModelOut.from_domain(created)


@router.patch("/models/{model_id}", response_model=ModelOut, summary="Update a model")
async def patch_model(
    model_id: UUID, payload: ModelPatch, actor: PlatformAdmin, service: AdminService, audit: Audit
) -> ModelOut:
    try:
        updated = await service.patch_model(model_id, payload, actor_user_id=actor.user_id)
    except CatalogError as exc:
        raise to_http(exc) from exc
    await audit.record(
        actor_user_id=actor.user_id,
        action="model_updated",
        target_kind="ai_model",
        target_id=str(model_id),
        after={"fields": sorted(payload.model_dump(exclude_unset=True))},
    )
    return ModelOut.from_domain(updated)


@router.delete(
    "/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a model"
)
async def delete_model(
    model_id: UUID, actor: PlatformAdmin, service: AdminService, audit: Audit
) -> Response:
    try:
        await service.delete_model(model_id, actor_user_id=actor.user_id)
    except CatalogError as exc:
        raise to_http(exc) from exc
    await audit.record(
        actor_user_id=actor.user_id,
        action="model_deleted",
        target_kind="ai_model",
        target_id=str(model_id),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------- routing --
@router.get("/routing", response_model=list[RouteOut], summary="Routing chains by priority")
async def list_routing(
    _: PlatformAdmin, service: AdminService, purpose: str | None = None
) -> list[RouteOut]:
    target = _purpose_or_422(purpose) if purpose is not None else None
    return [RouteOut.from_domain(r) for r in await service.list_routes(target)]


@router.put(
    "/routing/{purpose}",
    response_model=list[RouteOut],
    summary="Replace the ordered model chain of a purpose (index = priority; empty = none)",
)
async def put_routing(
    purpose: str, payload: RoutingPut, actor: PlatformAdmin, service: AdminService, audit: Audit
) -> list[RouteOut]:
    target = _purpose_or_422(purpose)
    try:
        stored = await service.replace_routing(target, payload, actor_user_id=actor.user_id)
    except CatalogError as exc:
        raise to_http(exc) from exc
    await audit.record(
        actor_user_id=actor.user_id,
        action="routing_replaced",
        target_kind="ai_routing",
        target_id=target,
        after={"models": [str(r.model_id) for r in stored]},
    )
    return [RouteOut.from_domain(r) for r in stored]


# ----------------------------------------------------------------- probe --
@router.post(
    "/probe",
    response_model=ProbeOut,
    summary="Run a tiny completion through one model (5/min per admin; platform-paid)",
)
async def probe(  # noqa: PLR0913
    payload: ProbeRequest,
    request: Request,
    actor: PlatformAdmin,
    service: AdminService,
    settings: Annotated[Settings, Depends(get_settings)],
    writer: CostWriter,
    usage_reader: BudgetReader,
    limiter: Limiter,
) -> ProbeOut:
    retry = limiter.hit(PROBE_BUCKET, str(actor.user_id), PROBE_LIMIT, PROBE_WINDOW_SECONDS)
    if retry is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error": "rate_limited", "message": "too many probes; try again shortly"},
            headers={"Retry-After": str(retry)},
        )

    def guard_factory(inner: AIGateway, _model: str, snapshot: CatalogSnapshot) -> AIGateway:
        return build_budget_guard(
            inner,
            settings,
            writer=writer,
            usage_reader=usage_reader,
            limits_reader=None,
            request_id=request.headers.get("x-request-id"),
            price_table=CatalogPriceTable(snapshot),
        )

    try:
        return await service.probe(
            payload,
            actor_user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            guard_factory=guard_factory,
            http_client=getattr(request.app.state, "http_client", None),
        )
    except BudgetGuardError as exc:
        raise budget_error_to_http(exc) from exc
    except CatalogError as exc:
        raise to_http(exc) from exc


__all__ = ["PROBE_BUCKET", "PROBE_LIMIT", "PROBE_WINDOW_SECONDS", "router", "to_http"]
