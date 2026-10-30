"""Application service for tenancy (SPRINT-03 B5/B8/B9).

Access policy (B5) lives in :meth:`TenancyService.require_role`: ``None`` ⇒
``BrandNotFoundError`` (404, hides existence), insufficient role ⇒ ``InsufficientRoleError``
(403). Persistence is behind the ``domain.repository`` Protocols (D-013).
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from src.modules.tenancy.domain.access import (
    GRANT_ROLES,
    READ_ROLES,
    BrandAccessResolver,
    BrandRole,
)
from src.modules.tenancy.domain.models import Brand, BrandAccessGrant, GrantRole, Tenant
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
    TenantNotFoundError,
    TenantSlugTakenError,
)


class TenancyService:
    def __init__(
        self,
        tenants: TenantRepository,
        brands: BrandRepository,
        grants: BrandAccessGrantRepository,
        resolver: BrandAccessResolver,
    ) -> None:
        self._tenants = tenants
        self._brands = brands
        self._grants = grants
        self._resolver = resolver

    # ---------------------------------------------------------------- tenants --
    async def create_tenant(self, slug: str, name: str) -> Tenant:
        if await self._tenants.get_by_slug(slug) is not None:
            raise TenantSlugTakenError(f"tenant slug {slug!r} is already taken")
        return await self._tenants.add(Tenant(slug=slug, name=name))

    async def get_own_tenant(self, actor_tenant_id: UUID, tenant_id: UUID) -> Tenant:
        """A tenant can only read itself (B9: path id must equal the actor)."""
        if actor_tenant_id != tenant_id:
            raise TenantNotFoundError()
        tenant = await self._tenants.get(tenant_id)
        if tenant is None:
            raise TenantNotFoundError()
        return tenant

    # ----------------------------------------------------------------- brands --
    async def create_brand(self, owner_tenant_id: UUID, slug: str, name: str) -> Brand:
        if await self._tenants.get(owner_tenant_id) is None:
            raise TenantNotFoundError()
        if await self._brands.get_by_owner_and_slug(owner_tenant_id, slug) is not None:
            raise BrandSlugTakenError(f"brand slug {slug!r} already exists for this tenant")
        return await self._brands.add(Brand(owner_tenant_id=owner_tenant_id, slug=slug, name=name))

    async def list_brands(self, actor_tenant_id: UUID) -> list[tuple[Brand, BrandRole]]:
        return await self._brands.list_accessible(actor_tenant_id)

    async def get_brand(self, actor_tenant_id: UUID, brand_id: UUID) -> tuple[Brand, BrandRole]:
        role = await self.require_role(actor_tenant_id, brand_id, READ_ROLES)
        brand = await self._brands.get(brand_id)
        if brand is None:  # pragma: no cover - resolver already proved existence
            raise BrandNotFoundError()
        return brand, role

    # ----------------------------------------------------------------- access --
    async def require_role(
        self, actor_tenant_id: UUID, brand_id: UUID, allowed: Iterable[BrandRole]
    ) -> BrandRole:
        role = await self._resolver.resolve(actor_tenant_id, brand_id)
        if role is None:
            raise BrandNotFoundError()
        if role not in frozenset(allowed):
            raise InsufficientRoleError(f"role {role.value!r} may not perform this action")
        return role

    # ----------------------------------------------------------------- grants --
    async def grant_access(
        self, actor_tenant_id: UUID, brand_id: UUID, grantee_tenant_id: UUID, role: GrantRole
    ) -> BrandAccessGrant:
        await self.require_role(actor_tenant_id, brand_id, GRANT_ROLES)
        if grantee_tenant_id == actor_tenant_id:
            raise GranteeIsOwnerError("the brand owner cannot be granted a role on its own brand")
        if await self._tenants.get(grantee_tenant_id) is None:
            raise TenantNotFoundError()
        if await self._grants.get_active(brand_id, grantee_tenant_id) is not None:
            raise GrantAlreadyActiveError()
        return await self._grants.add(
            BrandAccessGrant(
                brand_id=brand_id,
                grantee_tenant_id=grantee_tenant_id,
                role=role,
                granted_by_tenant_id=actor_tenant_id,
            )
        )

    async def list_grants(self, actor_tenant_id: UUID, brand_id: UUID) -> list[BrandAccessGrant]:
        await self.require_role(actor_tenant_id, brand_id, GRANT_ROLES)
        return await self._grants.list_by_brand(brand_id)

    async def revoke_grant(self, actor_tenant_id: UUID, brand_id: UUID, grant_id: UUID) -> int:
        """B8: revoke and, in the same transaction, hard-delete the grantee's connections
        in *this* brand (their Meta tokens must not outlive the authorisation).

        Returns the number of connections removed.
        """
        await self.require_role(actor_tenant_id, brand_id, GRANT_ROLES)
        revoked = await self._grants.revoke(brand_id, grant_id)
        if revoked is None:
            raise GrantNotFoundError()
        return await self._grants.delete_connections_of_grantee(
            brand_id, revoked.grantee_tenant_id
        )
