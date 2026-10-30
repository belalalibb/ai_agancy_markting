"""Storage contracts for tenancy (ARCHITECTURE_LOCK D-013 — Protocols only).

The service depends on these ``Protocol``s; the PostgreSQL implementation is in
``infrastructure/postgres_repository.py`` and the unit tests use dict-backed fakes.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from src.modules.tenancy.domain.access import BrandRole
from src.modules.tenancy.domain.models import Brand, BrandAccessGrant, Tenant


@runtime_checkable
class TenantRepository(Protocol):
    async def add(self, tenant: Tenant) -> Tenant: ...

    async def get(self, tenant_id: UUID) -> Tenant | None: ...

    async def get_by_slug(self, slug: str) -> Tenant | None: ...


@runtime_checkable
class BrandRepository(Protocol):
    async def add(self, brand: Brand) -> Brand: ...

    async def get(self, brand_id: UUID) -> Brand | None: ...

    async def get_by_owner_and_slug(self, owner_tenant_id: UUID, slug: str) -> Brand | None: ...

    async def list_accessible(self, tenant_id: UUID) -> list[tuple[Brand, BrandRole]]:
        """Brands owned by ``tenant_id`` (OWNER) plus brands with an *active* grant."""
        ...


@runtime_checkable
class BrandAccessGrantRepository(Protocol):
    async def add(self, grant: BrandAccessGrant) -> BrandAccessGrant: ...

    async def get(self, brand_id: UUID, grant_id: UUID) -> BrandAccessGrant | None: ...

    async def get_active(
        self, brand_id: UUID, grantee_tenant_id: UUID
    ) -> BrandAccessGrant | None: ...

    async def list_by_brand(self, brand_id: UUID) -> list[BrandAccessGrant]:
        """Active *and* revoked grants (audit trail), oldest first."""
        ...

    async def revoke(self, brand_id: UUID, grant_id: UUID) -> BrandAccessGrant | None:
        """Set ``revoked_at = now()``; ``None`` when unknown or already revoked."""
        ...

    async def delete_connections_of_grantee(self, brand_id: UUID, grantee_tenant_id: UUID) -> int:
        """B8: hard-delete ``social_connections`` of ``grantee_tenant_id`` in this brand.

        Returns the number of connections removed (pages cascade). Runs in the *same*
        transaction as :meth:`revoke` (the request-scoped session).
        """
        ...
