"""Test doubles that live only in ``tests/`` (no InMemory repository in ``src/``).

All fakes honour the domain ``Protocol``s and are *brand/tenant-aware* (SPRINT-03).
``FakeStore`` ties them together so a single in-memory world backs every router test and
the B8 grant revoke can purge connections exactly like the SQL implementation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from src.modules.auth.domain.models import RefreshSession, User
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
        self.users: dict[UUID, User] = {}
        self.sessions: dict[UUID, RefreshSession] = {}


# --------------------------------------------------------------------- auth --
class FakeUserRepository:
    def __init__(self, store: FakeStore) -> None:
        self._s = store

    async def add(self, user: User) -> User:
        if any(u.email.lower() == user.email.lower() for u in self._s.users.values()):
            msg = "uq_users_email_lower"
            raise ValueError(msg)
        self._s.users[user.id] = user
        return user

    async def get(self, user_id: UUID) -> User | None:
        return self._s.users.get(user_id)

    async def get_by_email(self, email: str) -> User | None:
        needle = email.strip().lower()
        return next((u for u in self._s.users.values() if u.email.lower() == needle), None)

    async def update_password_hash(self, user_id: UUID, password_hash: str) -> None:
        user = self._s.users[user_id]
        self._s.users[user_id] = user.model_copy(update={"password_hash": password_hash})

    async def touch_last_login(self, user_id: UUID, at: datetime) -> None:
        user = self._s.users[user_id]
        self._s.users[user_id] = user.model_copy(update={"last_login_at": at})


class FakeRefreshSessionRepository:
    def __init__(self, store: FakeStore) -> None:
        self._s = store
        self.revoke_returns: int | None = None

    async def add(self, session: RefreshSession) -> RefreshSession:
        self._s.sessions[session.id] = session
        return session

    async def get(self, session_id: UUID) -> RefreshSession | None:
        return self._s.sessions.get(session_id)

    async def is_active(self, session_id: UUID, now: datetime) -> bool:
        session = self._s.sessions.get(session_id)
        if session is None or session.revoked_at is not None or session.is_expired(now):
            return False
        user = self._s.users.get(session.user_id)
        # H6: unknown user (ad-hoc test actors) counts as active; a disabled one does not.
        return user is None or user.is_active

    async def revoke(self, session_id: UUID, at: datetime, replaced_by: UUID | None = None) -> int:
        if self.revoke_returns is not None:  # H7-U1: simulate losing the UPDATE race
            return self.revoke_returns
        session = self._s.sessions.get(session_id)
        if session is not None and session.revoked_at is None:
            self._s.sessions[session_id] = session.model_copy(
                update={"revoked_at": at, "replaced_by": replaced_by}
            )
            return 1
        return 0

    async def link_replacement(self, session_id: UUID, replaced_by: UUID) -> None:
        session = self._s.sessions.get(session_id)
        if session is not None:
            self._s.sessions[session_id] = session.model_copy(update={"replaced_by": replaced_by})

    async def revoke_all_for_user(self, user_id: UUID, at: datetime) -> int:
        count = 0
        for sid, session in list(self._s.sessions.items()):
            if session.user_id == user_id and session.revoked_at is None:
                self._s.sessions[sid] = session.model_copy(update={"revoked_at": at})
                count += 1
        return count


class FakePasswordHasher:
    """Cheap, deterministic hasher that *counts* verify() calls (A-U4 constant-time check).

    ``hash()`` returns ``"fake$<password>"``; ``needs_rehash`` is true for ``"legacy$"`` hashes.
    """

    def __init__(self) -> None:
        self.verify_calls = 0
        self.hash_calls = 0

    def hash(self, password: str) -> str:
        self.hash_calls += 1
        return f"fake${password}"

    def verify(self, password_hash: str, password: str) -> bool:
        self.verify_calls += 1
        scheme, _, stored = password_hash.partition("$")
        return scheme in {"fake", "legacy"} and stored == password

    def needs_rehash(self, password_hash: str) -> bool:
        return password_hash.startswith("legacy$")


class FakeRateLimiter:
    """Never limits unless told to; records every call for assertions."""

    def __init__(self) -> None:
        self.hits: list[tuple[str, str]] = []
        self.failures: dict[tuple[str, str], int] = {}
        self.blocked: dict[tuple[str, str], int] = {}
        self.reject_after: int | None = None

    def hit(self, bucket: str, key: str, limit: int, window_seconds: int) -> int | None:
        self.hits.append((bucket, key))
        if self.reject_after is not None and len(self.hits) > self.reject_after:
            return window_seconds
        return None

    def record_failure(self, bucket: str, key: str, limit: int, block_seconds: int) -> None:
        n = self.failures.get((bucket, key), 0) + 1
        self.failures[(bucket, key)] = n
        if n >= limit:
            self.blocked[(bucket, key)] = block_seconds

    def blocked_for(self, bucket: str, key: str) -> int | None:
        return self.blocked.get((bucket, key))

    def reset_failures(self, bucket: str, key: str) -> None:
        self.failures.pop((bucket, key), None)
        self.blocked.pop((bucket, key), None)


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

    async def update_token_metadata(
        self,
        brand_id: UUID,
        account_id: UUID,
        *,
        token_expires_at: datetime | None,
        granted_scopes: list[str] | None,
        last_synced_at: datetime,
    ) -> bool:
        row = await self.get(brand_id, account_id)
        if row is None:
            return False
        update: dict[str, object] = {
            "token_expires_at": token_expires_at,
            "last_synced_at": last_synced_at,
            "updated_at": datetime.now(tz=UTC),
        }
        if granted_scopes is not None:
            update["granted_scopes"] = list(granted_scopes)
        self.rows[account_id] = row.model_copy(update=update)
        return True


class FakeStatusMarker:
    """H3: records out-of-band status writes and applies them to the shared store."""

    def __init__(self, store: FakeStore) -> None:
        self._s = store
        self.calls: list[tuple[UUID, UUID, AccountStatus]] = []

    async def mark_out_of_band(
        self, brand_id: UUID, account_id: UUID, status: AccountStatus
    ) -> bool:
        self.calls.append((brand_id, account_id, status))
        return await FakeConnectedAccountRepository(self._s).set_status(
            brand_id, account_id, status
        )


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
