"""HTTP routes for tenancy (``/api/v1/tenants*``, ``/api/v1/brands*``) — SPRINT-03 B9.

| Method | Path                                   | Role          |
|--------|----------------------------------------|---------------|
| POST   | /tenants                               | —             |
| GET    | /tenants/{tenant_id}?tenant_id=        | same tenant   |
| POST   | /brands                                | owner = body  |
| GET    | /brands?tenant_id=                     | —             |
| GET    | /brands/{brand_id}?tenant_id=          | any role      |
| POST   | /brands/{brand_id}/grants              | OWNER         |
| GET    | /brands/{brand_id}/grants?tenant_id=   | OWNER         |
| DELETE | /brands/{brand_id}/grants/{grant_id}   | OWNER (B8)    |

Error bodies: ``{"error": "<code>", "message": "..."}`` (B10).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from src.modules.tenancy.dependencies import (
    ActorTenantId,
    get_brand_access_resolver,
    get_brand_repository,
    get_grant_repository,
    get_tenant_repository,
)
from src.modules.tenancy.domain.access import BrandAccessResolver
from src.modules.tenancy.domain.repository import (
    BrandAccessGrantRepository,
    BrandRepository,
    TenantRepository,
)
from src.modules.tenancy.exceptions import (
    BrandNotFoundError,
    BrandSlugTakenError,
    GrantAlreadyActiveError,
    GranteeIsOwnerError,
    GrantNotFoundError,
    InsufficientRoleError,
    TenancyError,
    TenantNotFoundError,
    TenantSlugTakenError,
)
from src.modules.tenancy.schemas import (
    BrandCreate,
    BrandOut,
    BrandWithRoleOut,
    GrantCreate,
    GrantOut,
    TenantCreate,
    TenantOut,
)
from src.modules.tenancy.service import TenancyService

router = APIRouter(tags=["Tenancy"])


def get_tenancy_service(
    tenants: Annotated[TenantRepository, Depends(get_tenant_repository)],
    brands: Annotated[BrandRepository, Depends(get_brand_repository)],
    grants: Annotated[BrandAccessGrantRepository, Depends(get_grant_repository)],
    resolver: Annotated[BrandAccessResolver, Depends(get_brand_access_resolver)],
) -> TenancyService:
    return TenancyService(tenants=tenants, brands=brands, grants=grants, resolver=resolver)


Service = Annotated[TenancyService, Depends(get_tenancy_service)]


# ---------------------------------------------------------------- tenants --
@router.post(
    "/tenants",
    response_model=TenantOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a tenant",
)
async def create_tenant(payload: TenantCreate, service: Service) -> TenantOut:
    try:
        tenant = await service.create_tenant(payload.slug, payload.name)
    except TenancyError as exc:
        raise to_http(exc) from exc
    return TenantOut.from_domain(tenant)


@router.get("/tenants/{tenant_id}", response_model=TenantOut, summary="Read your own tenant")
async def get_tenant(tenant_id: UUID, actor: ActorTenantId, service: Service) -> TenantOut:
    try:
        tenant = await service.get_own_tenant(actor, tenant_id)
    except TenancyError as exc:
        raise to_http(exc) from exc
    return TenantOut.from_domain(tenant)


# ----------------------------------------------------------------- brands --
@router.post(
    "/brands",
    response_model=BrandOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a brand owned by the acting tenant",
)
async def create_brand(payload: BrandCreate, service: Service) -> BrandOut:
    try:
        brand = await service.create_brand(payload.tenant_id, payload.slug, payload.name)
    except TenancyError as exc:
        raise to_http(exc) from exc
    return BrandOut.from_domain(brand)


@router.get(
    "/brands",
    response_model=list[BrandWithRoleOut],
    summary="List brands the acting tenant owns or has an active grant on",
)
async def list_brands(actor: ActorTenantId, service: Service) -> list[BrandWithRoleOut]:
    pairs = await service.list_brands(actor)
    return [BrandWithRoleOut.from_domain_with_role(brand, role) for brand, role in pairs]


@router.get("/brands/{brand_id}", response_model=BrandWithRoleOut, summary="Read one brand")
async def get_brand(brand_id: UUID, actor: ActorTenantId, service: Service) -> BrandWithRoleOut:
    try:
        brand, role = await service.get_brand(actor, brand_id)
    except TenancyError as exc:
        raise to_http(exc) from exc
    return BrandWithRoleOut.from_domain_with_role(brand, role)


# ----------------------------------------------------------------- grants --
@router.post(
    "/brands/{brand_id}/grants",
    response_model=GrantOut,
    status_code=status.HTTP_201_CREATED,
    summary="Grant another tenant a role on this brand (owner only)",
)
async def create_grant(brand_id: UUID, payload: GrantCreate, service: Service) -> GrantOut:
    try:
        grant = await service.grant_access(
            payload.tenant_id, brand_id, payload.grantee_tenant_id, payload.role
        )
    except TenancyError as exc:
        raise to_http(exc) from exc
    return GrantOut.from_domain(grant)


@router.get(
    "/brands/{brand_id}/grants",
    response_model=list[GrantOut],
    summary="List grants (active and revoked) of this brand (owner only)",
)
async def list_grants(brand_id: UUID, actor: ActorTenantId, service: Service) -> list[GrantOut]:
    try:
        grants = await service.list_grants(actor, brand_id)
    except TenancyError as exc:
        raise to_http(exc) from exc
    return [GrantOut.from_domain(g) for g in grants]


@router.delete(
    "/brands/{brand_id}/grants/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a grant and purge the grantee's connections in this brand (owner only)",
)
async def revoke_grant(
    brand_id: UUID, grant_id: UUID, actor: ActorTenantId, service: Service
) -> Response:
    try:
        await service.revoke_grant(actor, brand_id, grant_id)
    except TenancyError as exc:
        raise to_http(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------- error mapping --
_STATUS: dict[type[TenancyError], int] = {
    TenantNotFoundError: status.HTTP_404_NOT_FOUND,
    BrandNotFoundError: status.HTTP_404_NOT_FOUND,
    GrantNotFoundError: status.HTTP_404_NOT_FOUND,
    TenantSlugTakenError: status.HTTP_409_CONFLICT,
    BrandSlugTakenError: status.HTTP_409_CONFLICT,
    GrantAlreadyActiveError: status.HTTP_409_CONFLICT,
    InsufficientRoleError: status.HTTP_403_FORBIDDEN,
    GranteeIsOwnerError: status.HTTP_422_UNPROCESSABLE_CONTENT,
}


def to_http(exc: TenancyError) -> HTTPException:
    """Unified mapping shared with ``social_connect`` (which re-raises tenancy errors)."""
    code = _STATUS.get(type(exc), status.HTTP_400_BAD_REQUEST)
    return HTTPException(code, detail={"error": exc.error, "message": exc.message})
