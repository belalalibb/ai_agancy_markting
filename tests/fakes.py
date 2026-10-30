"""Test doubles that live only in ``tests/`` (no InMemory repository in ``src/``).

All fakes honour the domain ``Protocol``s and are *brand/tenant-aware* (SPRINT-03).
``FakeStore`` ties them together so a single in-memory world backs every router test and
the B8 grant revoke can purge connections exactly like the SQL implementation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from src.modules.social_connect.domain.models import AccountStatus, ConnectedAccount, PageAsset
from src.modules.tenancy.domain.access import BrandRole
from src.modules.tenancy.domain.models import Brand, BrandAccessGrant, Tenant


class FakeStore:
    """Shared in-memory tables for all fakes (one instance per test)."""

    def __init__(self) -> None:
        self.tenants: dict[UUID, Tenant] = {}
        self.brands: dict[UUID, Brand] = {}
        self.grants: dict[UUID, BrandAccessGrant] = {}
        self.connections: dict[UUID, ConnectedAccount] = {}


# ------------------------------------------------------------------ tenancy --
class FakeTenantRepository:
    def __init__(self, store: FakeStore) -> None:
        self._s = store

    async def add(self, tenant: Tenant) -> Tenant:
        self._s.tenants[tenant.id] = tenant
        return tenant

    async def get(self, tenant_id: UUID) -> Tenant | None:
        return self._s.tenants.get(tenant_id)

    async def get_by_slug(self, slug: str) -> Tenant | None:
        return next((t for t in self._s.tenants.values() if t.slug == slug), None)


class FakeBrandRepository:
    def __init__(self, store: FakeStore) -> None:
        self._s = store

    async def add(self, brand: Brand) -> Brand:
        self._s.brands[brand.id] = brand
        return brand

    async def get(self, brand_id: UUID) -> Brand | None:
        return self._s.brands.get(brand_id)

    async def get_by_owner_and_slug(self, owner_tenant_id: UUID, slug: str) -> Brand | None:
        return next(
            (
                b
                for b in self._s.brands.values()
                if b.owner_tenant_id == owner_tenant_id and b.slug == slug
            ),
            None,
        )

    async def list_accessible(self, tenant_id: UUID) -> list[tuple[Brand, BrandRole]]:
        out: list[tuple[Brand, BrandRole]] = [
            (b, BrandRole.OWNER) for b in self._s.brands.values() if b.owner_tenant_id == tenant_id
        ]
        for g in self._s.grants.values():
            if g.grantee_tenant_id == tenant_id and g.revoked_at is None:
                out.append((self._s.brands[g.brand_id], BrandRole(g.role.value)))
        return out


class FakeBrandAccessGrantRepository:
    def __init__(self, store: FakeStore) -> None:
        self._s = store

    async def add(self, grant: BrandAccessGrant) -> BrandAccessGrant:
        self._s.grants[grant.id] = grant
        return grant

    async def get(self, brand_id: UUID, grant_id: UUID) -> BrandAccessGrant | None:
        g = self._s.grants.get(grant_id)
        return g if g is not None and g.brand_id == brand_id else None

    async def get_active(self, brand_id: UUID, grantee_tenant_id: UUID) -> BrandAccessGrant | None:
        return next(
            (
                g
                for g in self._s.grants.values()
                if g.brand_id == brand_id
                and g.grantee_tenant_id == grantee_tenant_id
                and g.revoked_at is None
            ),
            None,
        )

    async def list_by_brand(self, brand_id: UUID) -> list[BrandAccessGrant]:
        return sorted(
            (g for g in self._s.grants.values() if g.brand_id == brand_id),
            key=lambda g: g.granted_at,
        )

    async def revoke(self, brand_id: UUID, grant_id: UUID) -> BrandAccessGrant | None:
        g = await self.get(brand_id, grant_id)
        if g is None or g.revoked_at is not None:
            return None
        revoked = g.model_copy(update={"revoked_at": datetime.now(tz=UTC)})
        self._s.grants[grant_id] = revoked
        return revoked

    async def delete_connections_of_grantee(self, brand_id: UUID, grantee_tenant_id: UUID) -> int:
        doomed = [
            cid
            for cid, c in self._s.connections.items()
            if c.brand_id == brand_id and c.connected_by_tenant_id == grantee_tenant_id
        ]
        for cid in doomed:
            del self._s.connections[cid]
        return len(doomed)


class FakeBrandAccessResolver:
    def __init__(self, store: FakeStore) -> None:
        self._s = store

    async def resolve(self, tenant_id: UUID, brand_id: UUID) -> BrandRole | None:
        brand = self._s.brands.get(brand_id)
        if brand is None:
            return None
        if brand.owner_tenant_id == tenant_id:
            return BrandRole.OWNER
        for g in self._s.grants.values():
            if (
                g.brand_id == brand_id
                and g.grantee_tenant_id == tenant_id
                and g.revoked_at is None
            ):
                return BrandRole(g.role.value)
        return None


# ------------------------------------------------------------ social_connect --
class FakeConnectedAccountRepository:
    """Dict-backed stand-in honouring the ``ConnectedAccountRepository`` contract."""

    def __init__(self, store: FakeStore | None = None) -> None:
        self._s = store or FakeStore()
        self.upsert_calls = 0

    @property
    def rows(self) -> dict[UUID, ConnectedAccount]:
        return self._s.connections

    def assert_selection_invariant(self) -> None:
        """B-U12: no page anywhere with ``is_selected=False`` and a stored token."""
        for account in self.rows.values():
            offenders = account.violates_selection_invariant()
            assert offenders == [], f"{account.id}: unselected pages with token: {offenders}"

    async def upsert(self, account: ConnectedAccount) -> ConnectedAccount:
        self.upsert_calls += 1
        existing = await self.find_by_identity(
            account.brand_id, account.provider.value, account.provider_user_id
        )
        if existing is not None:
            merged = account.model_copy(update={"id": existing.id})
            self.rows[existing.id] = merged
            return merged
        self.rows[account.id] = account
        return account

    async def find_by_identity(
        self, brand_id: UUID, provider: str, provider_user_id: str
    ) -> ConnectedAccount | None:
        return next(
            (
                c
                for c in self.rows.values()
                if (c.brand_id, c.provider.value, c.provider_user_id)
                == (brand_id, provider, provider_user_id)
            ),
            None,
        )

    async def get(self, brand_id: UUID, account_id: UUID) -> ConnectedAccount | None:
        row = self.rows.get(account_id)
        return row if row is not None and row.brand_id == brand_id else None

    async def list_by_brand(self, brand_id: UUID) -> list[ConnectedAccount]:
        return [row for row in self.rows.values() if row.brand_id == brand_id]

    async def set_status(self, brand_id: UUID, account_id: UUID, status: AccountStatus) -> bool:
        row = await self.get(brand_id, account_id)
        if row is None:
            return False
        self.rows[account_id] = row.model_copy(update={"status": status})
        return True

    async def replace_pages(
        self, brand_id: UUID, account_id: UUID, pages: list[PageAsset]
    ) -> bool:
        row = await self.get(brand_id, account_id)
        if row is None:
            return False
        self.rows[account_id] = row.model_copy(
            update={"pages": list(pages), "last_synced_at": datetime.now(tz=UTC)}
        )
        return True

    async def delete(self, brand_id: UUID, account_id: UUID) -> bool:
        row = await self.get(brand_id, account_id)
        if row is None:
            return False
        del self.rows[account_id]
        return True


# ------------------------------------------------------------ stub session --
class StubResult:
    """Minimal stand-in for a SQLAlchemy ``Result`` (unit tests of the Postgres repos)."""

    def __init__(
        self,
        *,
        scalar: object = None,
        scalars: list[object] | None = None,
        rows: list[tuple[object, ...]] | None = None,
        rowcount: int | None = 0,
    ) -> None:
        self._scalar = scalar
        self._scalars = list(scalars or [])
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def scalar_one_or_none(self) -> object:
        return self._scalar

    def scalar_one(self) -> object:
        if self._scalar is None:
            msg = "No row was found"
            raise LookupError(msg)
        return self._scalar

    def scalars(self) -> StubResult:
        return self

    def __iter__(self):  # noqa: ANN204 - iterator protocol
        return iter(self._scalars)

    def all(self) -> list[object]:
        return list(self._rows) if self._rows else list(self._scalars)


class StubSession:
    """Records every statement; replays scripted :class:`StubResult`s in order.

    Lets the unit suite cover the Postgres repositories (statement composition and
    row→domain mapping) without a database. The integration suite exercises the real
    engine.
    """

    def __init__(self, results: list[StubResult] | None = None) -> None:
        self.results = list(results or [])
        self.executed: list[tuple[object, object]] = []
        self.added: list[object] = []
        self.refreshed: list[object] = []
        self.flush_count = 0
        self.identity: dict[tuple[type, object], object] = {}

    async def execute(self, statement: object, params: object = None) -> StubResult:
        self.executed.append((statement, params))
        return self.results.pop(0) if self.results else StubResult()

    def add(self, row: object) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        self.flush_count += 1

    async def refresh(self, row: object) -> None:
        self.refreshed.append(row)

    async def get(self, cls: type, key: object) -> object:
        return self.identity.get((cls, key))


def compiled(statement: object) -> str:
    """Render a statement as PostgreSQL SQL (for structural assertions)."""
    from sqlalchemy.dialects import postgresql

    return str(statement.compile(dialect=postgresql.dialect()))  # type: ignore[attr-defined]
