"""Unit tests — tenancy Postgres repositories against a scripted stub session.

These cover statement composition and row→domain mapping without a database;
the integration suite (``tests/integration``) runs the same code on PostgreSQL.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from src.modules.tenancy.dependencies import (
    get_brand_access_resolver,
    get_brand_repository,
    get_grant_repository,
    get_tenant_repository,
)
from src.modules.tenancy.domain.access import BrandRole
from src.modules.tenancy.domain.models import Brand, BrandAccessGrant, GrantRole, Tenant
from src.modules.tenancy.exceptions import (
    BrandSlugTakenError,
    GrantAlreadyActiveError,
    TenantSlugTakenError,
)
from src.modules.tenancy.infrastructure.orm import BrandAccessGrantRow, BrandRow, TenantRow
from src.modules.tenancy.infrastructure.postgres_repository import (
    PostgresBrandAccessGrantRepository,
    PostgresBrandAccessResolver,
    PostgresBrandRepository,
    PostgresTenantRepository,
)

from tests.conftest import AGENCY_ID, BRAND_ID, OWNER_ID
from tests.fakes import StubResult, StubSession, compiled

pytestmark = pytest.mark.unit

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _tenant_row(**overrides: object) -> TenantRow:
    values: dict[str, object] = {
        "id": OWNER_ID,
        "slug": "nile-owner",
        "name": "Nile",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return TenantRow(**values)


def _brand_row(**overrides: object) -> BrandRow:
    values: dict[str, object] = {
        "id": BRAND_ID,
        "owner_tenant_id": OWNER_ID,
        "slug": "nile-coffee",
        "name": "Nile Coffee",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return BrandRow(**values)


def _grant_row(**overrides: object) -> BrandAccessGrantRow:
    values: dict[str, object] = {
        "id": uuid4(),
        "brand_id": BRAND_ID,
        "grantee_tenant_id": AGENCY_ID,
        "role": "manager",
        "granted_by_tenant_id": OWNER_ID,
        "granted_at": NOW,
        "revoked_at": None,
    }
    values.update(overrides)
    return BrandAccessGrantRow(**values)


# ------------------------------------------------------------------ tenants --
async def test_tenant_repository_add_get_and_slug_lookup() -> None:
    session = StubSession(
        [
            StubResult(scalar=OWNER_ID),  # add ⇒ INSERT … RETURNING id
            StubResult(scalar=None),  # add (conflict) ⇒ no row
            StubResult(scalar=_tenant_row()),  # get_by_slug
        ]
    )
    repo = PostgresTenantRepository(session)  # type: ignore[arg-type]

    # H4: add() is INSERT … ON CONFLICT DO NOTHING RETURNING id, then re-read by pk.
    session.identity[(TenantRow, OWNER_ID)] = _tenant_row()
    stored = await repo.add(Tenant(id=OWNER_ID, slug="nile-owner", name="Nile"))
    assert stored.id == OWNER_ID
    assert stored.slug == "nile-owner"
    sql = compiled(session.executed[0][0])
    assert sql.startswith("INSERT INTO tenants") and "ON CONFLICT DO NOTHING" in sql
    assert "RETURNING tenants.id" in sql
    assert session.flush_count == 0 and session.added == []

    with pytest.raises(TenantSlugTakenError):
        await repo.add(Tenant(id=uuid4(), slug="nile-owner", name="Dup"))

    del session.identity[(TenantRow, OWNER_ID)]
    assert await repo.get(OWNER_ID) is None
    session.identity[(TenantRow, OWNER_ID)] = _tenant_row()
    got = await repo.get(OWNER_ID)
    assert got is not None and got.name == "Nile"

    by_slug = await repo.get_by_slug("nile-owner")
    assert by_slug is not None and by_slug.id == OWNER_ID
    assert "tenants.slug" in compiled(session.executed[2][0])
    assert await repo.get_by_slug("missing") is None


# ------------------------------------------------------------------- brands --
async def test_brand_repository_add_get_and_owner_slug_lookup() -> None:
    session = StubSession(
        [
            StubResult(scalar=BRAND_ID),  # add ⇒ RETURNING id
            StubResult(scalar=None),  # add (conflict)
            StubResult(scalar=_brand_row()),  # get_by_owner_and_slug
            StubResult(scalar=None),  # get_by_owner_and_slug (missing)
        ]
    )
    repo = PostgresBrandRepository(session)  # type: ignore[arg-type]

    session.identity[(BrandRow, BRAND_ID)] = _brand_row()
    stored = await repo.add(
        Brand(id=BRAND_ID, owner_tenant_id=OWNER_ID, slug="nile-coffee", name="Nile Coffee")
    )
    assert stored.owner_tenant_id == OWNER_ID
    sql = compiled(session.executed[0][0])
    assert sql.startswith("INSERT INTO brands") and "ON CONFLICT DO NOTHING" in sql
    with pytest.raises(BrandSlugTakenError):
        await repo.add(Brand(owner_tenant_id=OWNER_ID, slug="nile-coffee", name="Dup"))

    del session.identity[(BrandRow, BRAND_ID)]
    assert await repo.get(BRAND_ID) is None
    session.identity[(BrandRow, BRAND_ID)] = _brand_row()
    got = await repo.get(BRAND_ID)
    assert got is not None and got.slug == "nile-coffee"

    found = await repo.get_by_owner_and_slug(OWNER_ID, "nile-coffee")
    assert found is not None and found.id == BRAND_ID
    sql = compiled(session.executed[2][0])
    assert "brands.owner_tenant_id" in sql and "brands.slug" in sql
    assert await repo.get_by_owner_and_slug(OWNER_ID, "nope") is None


async def test_brand_repository_list_accessible_merges_owned_and_granted() -> None:
    other = _brand_row(id=uuid4(), owner_tenant_id=AGENCY_ID, slug="agency-brand")
    session = StubSession(
        [
            StubResult(scalars=[_brand_row()]),
            StubResult(rows=[(other, "viewer")]),
        ]
    )
    repo = PostgresBrandRepository(session)  # type: ignore[arg-type]

    result = await repo.list_accessible(OWNER_ID)

    assert [(b.slug, role) for b, role in result] == [
        ("nile-coffee", BrandRole.OWNER),
        ("agency-brand", BrandRole.VIEWER),
    ]
    granted_sql = compiled(session.executed[1][0])
    assert "brand_access_grants.revoked_at IS NULL" in granted_sql
    assert "JOIN brand_access_grants" in granted_sql


# ------------------------------------------------------------------- grants --
async def test_grant_repository_add_get_active_list_revoke() -> None:
    row = _grant_row()
    revoked = _grant_row(id=row.id, revoked_at=NOW)
    session = StubSession(
        [
            StubResult(scalar=row.id),  # add ⇒ RETURNING id
            StubResult(scalar=None),  # add (conflict on the active partial index)
            StubResult(scalar=row),  # get
            StubResult(scalar=None),  # get (missing)
            StubResult(scalar=row),  # get_active
            StubResult(scalars=[row]),  # list_by_brand
            StubResult(scalar=revoked),  # revoke
            StubResult(scalar=None),  # revoke (already revoked / missing)
        ]
    )
    repo = PostgresBrandAccessGrantRepository(session)  # type: ignore[arg-type]

    grant = BrandAccessGrant(
        id=row.id,
        brand_id=BRAND_ID,
        grantee_tenant_id=AGENCY_ID,
        role=GrantRole.MANAGER,
        granted_by_tenant_id=OWNER_ID,
        granted_at=NOW,
    )
    added = await repo.add(grant)
    assert added.role is GrantRole.MANAGER and added.id == row.id
    sql = compiled(session.executed[0][0])
    assert sql.startswith("INSERT INTO brand_access_grants") and "ON CONFLICT DO NOTHING" in sql
    with pytest.raises(GrantAlreadyActiveError):
        await repo.add(grant)

    got = await repo.get(BRAND_ID, row.id)
    assert got is not None and got.grantee_tenant_id == AGENCY_ID
    assert await repo.get(BRAND_ID, uuid4()) is None

    active = await repo.get_active(BRAND_ID, AGENCY_ID)
    assert active is not None and active.is_active
    assert "revoked_at IS NULL" in compiled(session.executed[4][0])

    listed = await repo.list_by_brand(BRAND_ID)
    assert [g.id for g in listed] == [row.id]

    revoked_grant = await repo.revoke(BRAND_ID, row.id)
    assert revoked_grant is not None and revoked_grant.revoked_at == NOW
    revoke_sql = compiled(session.executed[6][0])
    assert revoke_sql.startswith("UPDATE brand_access_grants")
    assert "revoked_at IS NULL" in revoke_sql and "RETURNING" in revoke_sql
    assert await repo.revoke(BRAND_ID, row.id) is None


async def test_grant_repository_purges_grantee_connections_with_raw_sql() -> None:
    session = StubSession([StubResult(rowcount=2), StubResult(rowcount=None)])
    repo = PostgresBrandAccessGrantRepository(session)  # type: ignore[arg-type]

    assert await repo.delete_connections_of_grantee(BRAND_ID, AGENCY_ID) == 2
    statement, params = session.executed[0]
    sql = str(statement)
    assert sql.startswith("DELETE FROM social_connections")
    assert "brand_id = :brand_id" in sql and "connected_by_tenant_id = :grantee_tenant_id" in sql
    assert params == {"brand_id": BRAND_ID, "grantee_tenant_id": AGENCY_ID}
    # ``rowcount`` may be None for some drivers → 0.
    assert await repo.delete_connections_of_grantee(BRAND_ID, AGENCY_ID) == 0


# ----------------------------------------------------------------- resolver --
@pytest.mark.parametrize(
    ("owner", "role", "expected"),
    [
        (None, None, None),
        (OWNER_ID, None, BrandRole.OWNER),
        (AGENCY_ID, "manager", BrandRole.MANAGER),
        (AGENCY_ID, "viewer", BrandRole.VIEWER),
        (AGENCY_ID, None, None),
    ],
)
async def test_resolver_owner_grant_or_none(
    owner: object, role: object, expected: BrandRole | None
) -> None:
    session = StubSession([StubResult(scalar=owner), StubResult(scalar=role)])
    resolver = PostgresBrandAccessResolver(session)  # type: ignore[arg-type]

    assert await resolver.resolve(OWNER_ID, BRAND_ID) is expected
    if owner is None or owner == OWNER_ID:
        assert len(session.executed) == 1  # short-circuits before the grant lookup
    else:
        assert "revoked_at IS NULL" in compiled(session.executed[1][0])


# ------------------------------------------------------------- dependencies --
def test_dependency_factories_bind_session_to_postgres_adapters() -> None:
    session = StubSession()
    assert isinstance(get_tenant_repository(session), PostgresTenantRepository)  # type: ignore[arg-type]
    assert isinstance(get_brand_repository(session), PostgresBrandRepository)  # type: ignore[arg-type]
    assert isinstance(
        get_grant_repository(session),  # type: ignore[arg-type]
        PostgresBrandAccessGrantRepository,
    )
    assert isinstance(
        get_brand_access_resolver(session),  # type: ignore[arg-type]
        PostgresBrandAccessResolver,
    )
