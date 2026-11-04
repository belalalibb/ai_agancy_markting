"""HTTP routes for entitlements (SPRINT-11 C-5) — three protected routes, no public ones.

| # | Method | Path                                         | Guard               |
|---|--------|----------------------------------------------|---------------------|
| 1 | POST   | /admin/entitlements/activate                 | ``PlatformAdmin``   |
| 2 | GET    | /admin/entitlements/{brand_id}               | ``PlatformAdmin``   |
| 3 | GET    | /brands/{brand_id}/entitlement               | ``READ_ROLES``      |

SPRINT-11.1 A-3 (E3): the brand consent ``PATCH`` route moved to ``tenancy``
(the column owner) so consent control survives removing this module (D-076).

Error bodies follow the platform convention ``{"error": "<code>", "message": "..."}``.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from src.modules.auth.dependencies import CurrentUser, PlatformAdmin
from src.modules.entitlements.dependencies import Counters, Service, UsageReader
from src.modules.entitlements.domain.models import utcnow
from src.modules.entitlements.exceptions import (
    BrandNotFoundError,
    EntitlementsError,
    InvalidActivationError,
)
from src.modules.entitlements.schemas import (
    ActivateRequest,
    BrandEntitlementOut,
    BrandEntitlementPublic,
)
from src.modules.entitlements.service import current_usage
from src.modules.ops.audit import Audit  # SPRINT-13 A-1
from src.modules.tenancy.domain.access import READ_ROLES
from src.modules.tenancy.exceptions import TenancyError
from src.modules.tenancy.router import Service as TenancyServiceDep
from src.modules.tenancy.router import to_http as tenancy_to_http

admin_router = APIRouter(prefix="/admin/entitlements", tags=["Platform admin"])
router = APIRouter(tags=["Entitlements (D-073)"])


# ------------------------------------------------------------------- admin --
@admin_router.post(
    "/activate",
    response_model=BrandEntitlementOut,
    summary="Activate or extend a brand's plan (platform admin; audited)",
)
async def activate_entitlement(
    payload: ActivateRequest, actor: PlatformAdmin, service: Service, audit: Audit
) -> BrandEntitlementOut:
    try:
        ent = await service.activate(
            payload.brand_id,
            payload.plan,
            payload.months,
            actor_user_id=actor.user_id,
            note=payload.note,
        )
    except EntitlementsError as exc:
        raise to_http(exc) from exc
    await audit.record(
        actor_user_id=actor.user_id,
        action="entitlement_activated",
        target_kind="brand",
        target_id=str(payload.brand_id),
        after={"plan": str(ent.plan), "months": payload.months},
    )
    return BrandEntitlementOut.from_domain(ent, await service.recent_events(payload.brand_id))


@admin_router.get(
    "/{brand_id}",
    response_model=BrandEntitlementOut,
    summary="Read a brand's entitlement + its last 20 audit events (platform admin)",
)
async def read_entitlement_admin(
    brand_id: UUID, actor: PlatformAdmin, service: Service
) -> BrandEntitlementOut:
    try:
        ent = await service.get(brand_id)
    except EntitlementsError as exc:
        raise to_http(exc) from exc
    return BrandEntitlementOut.from_domain(ent, await service.recent_events(brand_id))


# ------------------------------------------------------------------- brand --
@router.get(
    "/brands/{brand_id}/entitlement",
    response_model=BrandEntitlementPublic,
    summary="Plan, limits and current usage of this brand (any role)",
)
async def read_entitlement(
    brand_id: UUID,
    actor: CurrentUser,
    tenancy: TenancyServiceDep,
    service: Service,
    usage_reader: UsageReader,
    counters: Counters,
) -> BrandEntitlementPublic:
    try:
        await tenancy.require_role(actor.tenant_id, brand_id, READ_ROLES)
    except TenancyError as exc:
        raise tenancy_to_http(exc) from exc
    try:
        ent = await service.get(brand_id)
    except EntitlementsError as exc:  # pragma: no cover - the resolver proved existence
        raise to_http(exc) from exc
    usage = await current_usage(ent, usage_reader=usage_reader, counters=counters, now=utcnow())
    return BrandEntitlementPublic.from_domain(ent, usage)


# ---------------------------------------------------------- error mapping --
_STATUS: dict[type[EntitlementsError], int] = {
    BrandNotFoundError: status.HTTP_404_NOT_FOUND,
    InvalidActivationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
}


def to_http(exc: EntitlementsError) -> HTTPException:
    code = _STATUS.get(type(exc), status.HTTP_400_BAD_REQUEST)
    return HTTPException(code, detail={"error": exc.error, "message": exc.message})
