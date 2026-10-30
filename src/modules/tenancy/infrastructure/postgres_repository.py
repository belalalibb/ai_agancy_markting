"""PostgreSQL implementations of the tenancy repositories + ``BrandAccessResolver``.

Never commits: the request-scoped ``get_db_session`` owns the transaction, so e.g.
"revoke grant + delete grantee connections" (B8) is atomic.

SPRINT-04.1 H4: every ``add`` is ``INSERT … ON CONFLICT DO NOTHING RETURNING id``. The
service's existence pre-checks are not atomic under concurrency; when the unique constraint
fires the repository raises the *domain* error (409) instead of letting ``IntegrityError``
surface as a 500. No ``rollback`` is needed — a no-op insert leaves the transaction healthy.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.tenancy.domain.access import BrandRole
from src.modules.tenancy.domain.models import Brand, BrandAccessGrant, GrantRole, Tenant
from src.modules.tenancy.exceptions import (
    BrandSlugTakenError,
    GrantAlreadyActiveError,
    TenantSlugTakenError,
)
from src.modules.tenancy.infrastructure.orm import BrandAccessGrantRow, BrandRow, TenantRow


class PostgresTenantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, tenant: Tenant) -> Tenant:
        stmt = (
            pg_insert(TenantRow)
            .values(id=tenant.id, slug=tenant.slug, name=tenant.name)
            .on_conflict_do_nothing()
            .returning(TenantRow.id)
        )
        inserted = (await self._session.execute(stmt)).scalar_one_or_none()
        if inserted is None:
            raise TenantSlugTakenError(f"tenant slug {tenant.slug!r} is already taken")
        row = await self._session.get(TenantRow, inserted)
        if row is None:  # pragma: no cover - the row was just inserted in this transaction
            raise RuntimeError("inserted tenant vanished within the transaction")
        return _tenant(row)

    async def get(self, tenant_id: UUID) -> Tenant | None:
        row = await self._session.get(TenantRow, tenant_id)
        return _tenant(row) if row else None

    async def get_by_slug(self, slug: str) -> Tenant | None:
        row = (
            await self._session.execute(select(TenantRow).where(TenantRow.slug == slug))
        ).scalar_one_or_none()
        return _tenant(row) if row else None


class PostgresBrandRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, brand: Brand) -> Brand:
        stmt = (
            pg_insert(BrandRow)
            .values(
                id=brand.id,
                owner_tenant_id=brand.owner_tenant_id,
                slug=brand.slug,
                name=brand.name,
            )
            .on_conflict_do_nothing()
            .returning(BrandRow.id)
        )
        inserted = (await self._session.execute(stmt)).scalar_one_or_none()
        if inserted is None:
            raise BrandSlugTakenError(f"brand slug {brand.slug!r} already exists for this tenant")
        row = await self._session.get(BrandRow, inserted)
        if row is None:  # pragma: no cover - the row was just inserted in this transaction
            raise RuntimeError("inserted brand vanished within the transaction")
        return _brand(row)

    async def get(self, brand_id: UUID) -> Brand | None:
        row = await self._session.get(BrandRow, brand_id)
        return _brand(row) if row else None

    async def get_by_owner_and_slug(self, owner_tenant_id: UUID, slug: str) -> Brand | None:
        row = (
            await self._session.execute(
                select(BrandRow).where(
                    BrandRow.owner_tenant_id == owner_tenant_id, BrandRow.slug == slug
                )
            )
        ).scalar_one_or_none()
        return _brand(row) if row else None

    async def list_accessible(self, tenant_id: UUID) -> list[tuple[Brand, BrandRole]]:
        owned = (
            await self._session.execute(
                select(BrandRow)
                .where(BrandRow.owner_tenant_id == tenant_id)
                .order_by(BrandRow.created_at, BrandRow.id)
            )
        ).scalars()
        result: list[tuple[Brand, BrandRole]] = [(_brand(r), BrandRole.OWNER) for r in owned]
        granted = await self._session.execute(
            select(BrandRow, BrandAccessGrantRow.role)
            .join(BrandAccessGrantRow, BrandAccessGrantRow.brand_id == BrandRow.id)
            .where(
                BrandAccessGrantRow.grantee_tenant_id == tenant_id,
                BrandAccessGrantRow.revoked_at.is_(None),
            )
            .order_by(BrandRow.created_at, BrandRow.id)
        )
        result.extend((_brand(row), BrandRole(role)) for row, role in granted.all())
        return result


class PostgresBrandAccessGrantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, grant: BrandAccessGrant) -> BrandAccessGrant:
        stmt = (
            pg_insert(BrandAccessGrantRow)
            .values(
                id=grant.id,
                brand_id=grant.brand_id,
                grantee_tenant_id=grant.grantee_tenant_id,
                role=grant.role.value,
                granted_by_tenant_id=grant.granted_by_tenant_id,
                granted_at=grant.granted_at,
                revoked_at=grant.revoked_at,
            )
            .on_conflict_do_nothing()  # partial unique index: one *active* grant per grantee
            .returning(BrandAccessGrantRow.id)
        )
        inserted = (await self._session.execute(stmt)).scalar_one_or_none()
        if inserted is None:
            raise GrantAlreadyActiveError()
        return grant.model_copy(update={"id": inserted})

    async def get(self, brand_id: UUID, grant_id: UUID) -> BrandAccessGrant | None:
        row = (
            await self._session.execute(
                select(BrandAccessGrantRow).where(
                    BrandAccessGrantRow.brand_id == brand_id, BrandAccessGrantRow.id == grant_id
                )
            )
        ).scalar_one_or_none()
        return _grant(row) if row else None

    async def get_active(self, brand_id: UUID, grantee_tenant_id: UUID) -> BrandAccessGrant | None:
        row = (
            await self._session.execute(
                select(BrandAccessGrantRow).where(
                    BrandAccessGrantRow.brand_id == brand_id,
                    BrandAccessGrantRow.grantee_tenant_id == grantee_tenant_id,
                    BrandAccessGrantRow.revoked_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        return _grant(row) if row else None

    async def list_by_brand(self, brand_id: UUID) -> list[BrandAccessGrant]:
        rows = (
            await self._session.execute(
                select(BrandAccessGrantRow)
                .where(BrandAccessGrantRow.brand_id == brand_id)
                .order_by(BrandAccessGrantRow.granted_at, BrandAccessGrantRow.id)
            )
        ).scalars()
        return [_grant(r) for r in rows]

    async def revoke(self, brand_id: UUID, grant_id: UUID) -> BrandAccessGrant | None:
        row = (
            await self._session.execute(
                update(BrandAccessGrantRow)
                .where(
                    BrandAccessGrantRow.brand_id == brand_id,
                    BrandAccessGrantRow.id == grant_id,
                    BrandAccessGrantRow.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(tz=UTC))
                .returning(BrandAccessGrantRow)
            )
        ).scalar_one_or_none()
        return _grant(row) if row else None

    async def delete_connections_of_grantee(self, brand_id: UUID, grantee_tenant_id: UUID) -> int:
        # Raw SQL on purpose: ``tenancy`` must not import ``social_connect`` (B1 import
        # direction). Pages are removed by the FK ``ON DELETE CASCADE``.
        result = await self._session.execute(
            text(
                "DELETE FROM social_connections "
                "WHERE brand_id = :brand_id AND connected_by_tenant_id = :grantee_tenant_id"
            ),
            {"brand_id": brand_id, "grantee_tenant_id": grantee_tenant_id},
        )
        return result.rowcount or 0


class PostgresBrandAccessResolver:
    """B5: owner ⇒ OWNER; active grant ⇒ its role; else ``None``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve(self, tenant_id: UUID, brand_id: UUID) -> BrandRole | None:
        owner = (
            await self._session.execute(
                select(BrandRow.owner_tenant_id).where(BrandRow.id == brand_id)
            )
        ).scalar_one_or_none()
        if owner is None:
            return None
        if owner == tenant_id:
            return BrandRole.OWNER
        role = (
            await self._session.execute(
                select(BrandAccessGrantRow.role).where(
                    BrandAccessGrantRow.brand_id == brand_id,
                    BrandAccessGrantRow.grantee_tenant_id == tenant_id,
                    BrandAccessGrantRow.revoked_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        return BrandRole(role) if role else None


# ------------------------------------------------------------------ mappers --
def _tenant(row: TenantRow) -> Tenant:
    return Tenant(
        id=row.id,
        slug=row.slug,
        name=row.name,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _brand(row: BrandRow) -> Brand:
    return Brand(
        id=row.id,
        owner_tenant_id=row.owner_tenant_id,
        slug=row.slug,
        name=row.name,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _grant(row: BrandAccessGrantRow) -> BrandAccessGrant:
    return BrandAccessGrant(
        id=row.id,
        brand_id=row.brand_id,
        grantee_tenant_id=row.grantee_tenant_id,
        role=GrantRole(row.role),
        granted_by_tenant_id=row.granted_by_tenant_id,
        granted_at=row.granted_at,
        revoked_at=row.revoked_at,
    )
