"""Integration tests — real PostgreSQL: migrations, repositories, full API flow.

SPRINT-03: everything is brand-scoped (``brand_id`` + ``connected_by_tenant_id``) and
the tenancy world (tenants / brands / grants) is seeded through raw SQL.

Run with::

    TEST_DATABASE_URL=postgresql+asyncpg://aigos:aigos_dev_only@localhost:5432/ai_growth_os \\
        pytest -m integration -q
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
import respx
from alembic import command
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from src.core.oauth_state import OAuthStateSigner
from src.core.security import TokenEncryptionService
from src.modules.social_connect.domain.models import (
    AccountStatus,
    ConnectedAccount,
    InstagramAsset,
    PageAsset,
)
from src.modules.social_connect.domain.repository import ConnectedAccountRepository
from src.modules.social_connect.infrastructure.postgres_repository import (
    PostgresConnectedAccountRepository,
)
from src.modules.tenancy.domain.access import BrandRole
from src.modules.tenancy.domain.models import BrandAccessGrant, GrantRole
from src.modules.tenancy.exceptions import GrantAlreadyActiveError
from src.modules.tenancy.infrastructure.postgres_repository import (
    PostgresBrandAccessGrantRepository,
    PostgresBrandAccessResolver,
    PostgresBrandRepository,
)
from tests.conftest import (
    AGENCY_ID,
    BRAND2_ID,
    BRAND_ID,
    FAKE_STATE_SECRET,
    OWNER_ID,
    STRANGER_ID,
    debug_token_payload,
    me_payload,
    pages_payload,
    token_payload,
)
from tests.integration.conftest import (
    TABLES,
    TEST_DATABASE_URL,
    alembic_config,
    integration_headers,
    reset_db_caches,
    seed_tenancy,
)

pytestmark = pytest.mark.integration

API = "/api/v1"
CALLBACK = f"{API}/social/meta/callback"
SIGNER = OAuthStateSigner(FAKE_STATE_SECRET)


def brand_base(brand_id: UUID = BRAND_ID) -> str:
    return f"{API}/brands/{brand_id}/social/meta"


def _account(
    brand: UUID = BRAND_ID,
    user: str = "u-1",
    *,
    connected_by: UUID = OWNER_ID,
    pages: list[PageAsset] | None = None,
    token: str = "gAAAAA-cipher",  # noqa: S107 - fake ciphertext, not a secret
) -> ConnectedAccount:
    return ConnectedAccount(
        brand_id=brand,
        connected_by_tenant_id=connected_by,
        provider_user_id=user,
        provider_user_name="Owner",
        encrypted_user_token=token,
        token_expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        granted_scopes=["pages_show_list"],
        pages=pages
        if pages is not None
        else [
            PageAsset(
                page_id="1001",
                page_name="Nile",
                category="Coffee shop",
                tasks=["ANALYZE", "MANAGE"],
                encrypted_page_token="gAAAAA-page1",
                is_selected=True,
                instagram=InstagramAsset(ig_user_id="178", username="nile", name="Nile Coffee"),
            ),
            PageAsset(page_id="1002", page_name="Cairo", encrypted_page_token=None),
        ],
    )


async def _count(engine: AsyncEngine, table: str) -> int:
    assert table in TABLES  # constant, test-controlled identifiers only
    async with engine.connect() as conn:
        return (await conn.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar_one()  # noqa: S608


def _run(sql: str, params: dict | None = None, *, scalar: bool = False) -> object:
    """Execute one statement on a fresh loop + engine (for sync alembic/API tests)."""

    async def _go() -> object:
        eng = create_async_engine(TEST_DATABASE_URL)
        try:
            async with eng.begin() as conn:
                result = await conn.execute(text(sql), params or {})
                return result.scalar_one() if scalar else None
        finally:
            await eng.dispose()

    return asyncio.run(_go())


def _rows(sql: str, params: dict | None = None) -> list[tuple]:
    async def _go() -> list[tuple]:
        eng = create_async_engine(TEST_DATABASE_URL)
        try:
            async with eng.connect() as conn:
                return [tuple(r) for r in await conn.execute(text(sql), params or {})]
        finally:
            await eng.dispose()

    return asyncio.run(_go())


def _seed_fresh() -> None:
    """Seed the tenancy world on a fresh loop (sync API tests cannot use async fixtures)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    async def _go() -> None:
        eng = create_async_engine(TEST_DATABASE_URL)
        try:
            async with async_sessionmaker(eng, expire_on_commit=False)() as session:
                await seed_tenancy(session)
        finally:
            await eng.dispose()

    asyncio.run(_go())


def _mock_graph(graph_mock: respx.MockRouter, *, pages: int = 2, user_id: str = "90001") -> None:
    graph_mock.post(
        "/oauth/access_token", data__contains={"grant_type": "fb_exchange_token"}
    ).mock(return_value=httpx.Response(200, json=token_payload("LONG_LIVED", 5183944)))
    graph_mock.post("/oauth/access_token").mock(
        return_value=httpx.Response(200, json=token_payload("SHORT_LIVED", 5000))
    )
    graph_mock.get("/me", params__contains={"fields": "id,name"}).mock(
        return_value=httpx.Response(200, json=me_payload(user_id=user_id))
    )
    graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(200, json=debug_token_payload(user_id=user_id))
    )
    graph_mock.get("/me/accounts").mock(
        return_value=httpx.Response(200, json=pages_payload(pages))
    )


# ------------------------------------------------------------------ I1 ---
def test_alembic_upgrade_head_and_downgrade_base() -> None:
    """Round-trip the whole chain on the real database; ``vector`` must exist after upgrade."""
    cfg = alembic_config(TEST_DATABASE_URL)

    def _tables() -> set[str]:
        names = {
            r[0] for r in _rows("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        }
        ext = {
            f"ext:{r[0]}"
            for r in _rows("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        }
        return names | ext

    command.downgrade(cfg, "base")
    after_down = _tables()
    assert not set(TABLES) & after_down

    command.upgrade(cfg, "head")
    after_up = _tables()
    assert set(TABLES) <= after_up
    assert "ext:vector" in after_up


# ------------------------------------------------------------------ I2 ---
async def test_repo_upsert_inserts_connection_and_pages(
    seeded_session: AsyncSession, engine: AsyncEngine
) -> None:
    repo: ConnectedAccountRepository = PostgresConnectedAccountRepository(seeded_session)
    stored = await repo.upsert(_account())
    await seeded_session.commit()

    assert stored.brand_id == BRAND_ID
    assert stored.connected_by_tenant_id == OWNER_ID
    assert stored.status is AccountStatus.ACTIVE
    assert [p.page_id for p in stored.pages] == ["1001", "1002"]
    assert stored.pages[0].instagram is not None
    assert stored.pages[0].instagram.username == "nile"
    assert stored.pages[0].tasks == ["ANALYZE", "MANAGE"]
    assert stored.pages[0].is_selected is True
    assert stored.pages[1].instagram is None
    assert stored.pages[1].is_selected is False
    assert stored.token_expires_at == datetime(2030, 1, 1, tzinfo=UTC)
    assert await _count(engine, "social_connections") == 1
    assert await _count(engine, "social_connection_pages") == 2


# ------------------------------------------------------------- I3 / I-B2 ---
async def test_repo_upsert_same_user_same_brand_updates_two_brands_two_rows(
    seeded_session: AsyncSession, engine: AsyncEngine
) -> None:
    """I-B2 — ``uq_social_connections_brand_identity``: ON CONFLICT per brand."""
    repo = PostgresConnectedAccountRepository(seeded_session)
    first = await repo.upsert(_account(token="cipher-1"))
    await seeded_session.commit()

    replacement = _account(
        token="cipher-2",
        connected_by=AGENCY_ID,  # an agency re-connects the same Meta account
        pages=[PageAsset(page_id="2001", page_name="New", encrypted_page_token="p")],
    )
    second = await repo.upsert(replacement)  # different ``id`` on purpose
    await seeded_session.commit()

    assert second.id == first.id  # the unique constraint collapsed the rows
    assert second.encrypted_user_token == "cipher-2"
    assert second.connected_by_tenant_id == AGENCY_ID
    assert [p.page_id for p in second.pages] == ["2001"]  # pages replaced wholesale
    assert second.connected_at == first.connected_at  # original connect time kept
    assert second.updated_at >= first.updated_at
    assert await _count(engine, "social_connections") == 1
    assert await _count(engine, "social_connection_pages") == 1

    other_brand = await repo.upsert(_account(brand=BRAND2_ID, token="cipher-3"))
    await seeded_session.commit()
    assert other_brand.id != first.id
    assert await _count(engine, "social_connections") == 2


# ------------------------------------------------------------------ I4 ---
async def test_repo_brand_isolation(seeded_session: AsyncSession) -> None:
    repo = PostgresConnectedAccountRepository(seeded_session)
    a = await repo.upsert(_account(BRAND_ID, "same-user"))
    b = await repo.upsert(_account(BRAND2_ID, "same-user"))
    await seeded_session.commit()

    assert a.id != b.id
    assert [x.id for x in await repo.list_by_brand(BRAND_ID)] == [a.id]
    assert [x.id for x in await repo.list_by_brand(BRAND2_ID)] == [b.id]
    assert await repo.get(BRAND_ID, b.id) is None
    assert await repo.find_by_identity(BRAND_ID, "meta", "same-user") is not None
    assert await repo.find_by_identity(BRAND_ID, "meta", "nobody") is None
    assert await repo.delete(BRAND_ID, b.id) is False
    assert await repo.set_status(BRAND_ID, b.id, AccountStatus.REVOKED) is False
    assert await repo.replace_pages(BRAND_ID, b.id, []) is False
    assert (await repo.get(BRAND2_ID, b.id)).status is AccountStatus.ACTIVE  # type: ignore[union-attr]
    assert await repo.list_by_brand(uuid4()) == []


# ------------------------------------------------------------------ I5 ---
async def test_repo_delete_cascades_pages(
    seeded_session: AsyncSession, engine: AsyncEngine
) -> None:
    repo = PostgresConnectedAccountRepository(seeded_session)
    stored = await repo.upsert(_account())
    await seeded_session.commit()
    assert await _count(engine, "social_connection_pages") == 2

    assert await repo.delete(BRAND_ID, stored.id) is True
    await seeded_session.commit()
    assert await _count(engine, "social_connections") == 0
    assert await _count(engine, "social_connection_pages") == 0
    assert await repo.delete(BRAND_ID, stored.id) is False
    assert await repo.get(BRAND_ID, uuid4()) is None


# ------------------------------------------------------------------ I6 ---
async def test_repo_set_status_and_replace_pages(seeded_session: AsyncSession) -> None:
    repo = PostgresConnectedAccountRepository(seeded_session)
    stored = await repo.upsert(_account())
    await seeded_session.commit()

    assert await repo.set_status(BRAND_ID, stored.id, AccountStatus.REAUTH_REQUIRED) is True
    await seeded_session.commit()
    fetched = await repo.get(BRAND_ID, stored.id)
    assert fetched is not None
    assert fetched.status is AccountStatus.REAUTH_REQUIRED
    assert fetched.updated_at >= stored.updated_at
    assert await repo.set_status(BRAND_ID, uuid4(), AccountStatus.REVOKED) is False

    new_pages = [
        PageAsset(page_id="1001", page_name="Nile", encrypted_page_token=None),
        PageAsset(
            page_id="1002", page_name="Cairo", encrypted_page_token="gAAAAA-2", is_selected=True
        ),
    ]
    assert await repo.replace_pages(BRAND_ID, stored.id, new_pages) is True
    await seeded_session.commit()
    refetched = await repo.get(BRAND_ID, stored.id)
    assert refetched is not None
    by_id = {p.page_id: p for p in refetched.pages}
    assert by_id["1001"].is_selected is False and by_id["1001"].encrypted_page_token is None
    assert by_id["1002"].is_selected is True and by_id["1002"].encrypted_page_token == "gAAAAA-2"
    assert refetched.last_synced_at is not None


# ------------------------------------------------------------------ I7 ---
async def test_stored_tokens_are_ciphertext_and_decryptable(
    seeded_session: AsyncSession,
    engine: AsyncEngine,
    integration_encryption: TokenEncryptionService,
) -> None:
    raw_user, raw_page = "EAAG-user-token-plaintext", "EAAG-page-token-plaintext"
    account = _account(
        token=integration_encryption.encrypt_token(raw_user),
        pages=[
            PageAsset(
                page_id="1",
                page_name="P",
                encrypted_page_token=integration_encryption.encrypt_token(raw_page),
                is_selected=True,
            )
        ],
    )
    await PostgresConnectedAccountRepository(seeded_session).upsert(account)
    await seeded_session.commit()

    async with engine.connect() as conn:
        user_cell = (
            await conn.execute(text("SELECT encrypted_user_token FROM social_connections"))
        ).scalar_one()
        page_cell = (
            await conn.execute(text("SELECT encrypted_page_token FROM social_connection_pages"))
        ).scalar_one()

    for cell, raw in ((user_cell, raw_user), (page_cell, raw_page)):
        assert cell != raw
        assert raw not in cell
        assert cell.startswith("gAAAA")  # Fernet ciphertext marker
        assert integration_encryption.decrypt_token(cell) == raw


# ------------------------------------------------------------ SPRINT-02.1 ---
async def test_page_without_token_stored_as_null(
    seeded_session: AsyncSession,
    engine: AsyncEngine,
    integration_encryption: TokenEncryptionService,
) -> None:
    """H9 — a Page with no granted token round-trips as SQL ``NULL`` (never ``''``)."""
    repo: ConnectedAccountRepository = PostgresConnectedAccountRepository(seeded_session)
    account = _account(
        pages=[
            PageAsset(
                page_id="3001",
                page_name="With token",
                encrypted_page_token=integration_encryption.encrypt_token("PAGE_TOKEN_3001"),
                is_selected=True,
            ),
            PageAsset(page_id="3002", page_name="Without token", encrypted_page_token=None),
        ]
    )
    saved = await repo.upsert(account)
    await seeded_session.commit()

    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT page_id, encrypted_page_token, encrypted_page_token IS NULL AS is_null "
                "FROM social_connection_pages ORDER BY page_id"
            )
        )
        by_page = {r[0]: (r[1], r[2]) for r in rows}
    assert by_page["3002"] == (None, True)
    assert by_page["3001"][1] is False
    assert by_page["3001"][0].startswith("gAAAA")

    fetched = await repo.get(BRAND_ID, saved.id)
    assert fetched is not None
    pages = {p.page_id: p for p in fetched.pages}
    assert pages["3002"].encrypted_page_token is None
    assert integration_encryption.decrypt_token(pages["3001"].encrypted_page_token or "") == (
        "PAGE_TOKEN_3001"
    )


# ---------------------------------------------------------------- I-B1 ---
def test_alembic_0003_migrates_legacy_rows_and_roundtrips() -> None:
    """I-B1 — 0002 → 0003 with legacy rows (two tenants, page with/without token) → down → up.

    Also exercises owner note 3: on downgrade two brands of one owner sharing the same Meta
    account collapse to one ``(tenant_id, provider, provider_user_id)`` row (newest kept).
    """
    cfg = alembic_config(TEST_DATABASE_URL)
    old_tables = ("social_connection_pages", "social_connections")
    columns_sql = (
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'social_connections'"
    )

    command.downgrade(cfg, "0002")
    assert _run("SELECT version_num FROM alembic_version", scalar=True) == "0002"
    _run(f"TRUNCATE {', '.join(old_tables)} CASCADE")

    # Legacy world: two free-text tenants, one connection each; tenant 'a' has two pages.
    _run(
        "INSERT INTO social_connections (id, tenant_id, provider, provider_user_id, "
        "encrypted_user_token, granted_scopes, status, connected_at, updated_at) VALUES "
        "('00000000-0000-0000-0000-0000000000a1', 'Tenant A!', 'meta', 'u-a', 'gAAAAA-a', "
        "'[]'::jsonb, 'active', now(), now()), "
        "('00000000-0000-0000-0000-0000000000b1', 'tenant-b', 'meta', 'u-b', 'gAAAAA-b', "
        "'[]'::jsonb, 'active', now(), now())"
    )
    _run(
        "INSERT INTO social_connection_pages (id, connection_id, page_id, page_name, tasks, "
        "encrypted_page_token) VALUES "
        "('00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-0000000000a1', "
        "'p-tok', 'With token', '[]'::jsonb, 'gAAAAA-p'), "
        "('00000000-0000-0000-0000-0000000000a3', '00000000-0000-0000-0000-0000000000a1', "
        "'p-null', 'No token', '[]'::jsonb, NULL)"
    )

    command.upgrade(cfg, "0003")

    tenants = {r[1]: r for r in _rows("SELECT id, slug, name FROM tenants ORDER BY slug")}
    assert set(tenants) == {"tenant-a", "tenant-b"}  # 'Tenant A!' slugified
    assert tenants["tenant-a"][2] == "Tenant A!"  # legacy id kept as display name
    brands = _rows("SELECT id, owner_tenant_id, slug FROM brands ORDER BY owner_tenant_id")
    assert len(brands) == 2 and {b[2] for b in brands} == {"default"}
    assert {b[1] for b in brands} == {tenants["tenant-a"][0], tenants["tenant-b"][0]}
    linked = _rows(
        "SELECT sc.provider_user_id, t.slug, sc.connected_by_tenant_id = b.owner_tenant_id "
        "FROM social_connections sc "
        "JOIN brands b ON b.id = sc.brand_id JOIN tenants t ON t.id = b.owner_tenant_id "
        "ORDER BY sc.provider_user_id"
    )
    assert linked == [("u-a", "tenant-a", True), ("u-b", "tenant-b", True)]
    cols = {r[0] for r in _rows(columns_sql)}
    assert "tenant_id" not in cols and {"brand_id", "connected_by_tenant_id"} <= cols
    selected = dict(_rows("SELECT page_id, is_selected FROM social_connection_pages"))
    assert selected == {"p-tok": True, "p-null": False}  # legacy token ⇒ selected

    # Owner note 3 setup: a second brand of tenant-a linking the *same* Meta account, newer.
    owner_a = tenants["tenant-a"][0]
    _run(
        "INSERT INTO brands (id, owner_tenant_id, slug, name) VALUES "
        "('00000000-0000-0000-0000-0000000000c0', :owner, 'second', 'Second')",
        {"owner": owner_a},
    )
    _run(
        "INSERT INTO social_connections (id, brand_id, connected_by_tenant_id, provider, "
        "provider_user_id, encrypted_user_token, granted_scopes, status, connected_at, "
        "updated_at) VALUES ('00000000-0000-0000-0000-0000000000c1', "
        "'00000000-0000-0000-0000-0000000000c0', :owner, 'meta', 'u-a', 'gAAAAA-newest', "
        "'[]'::jsonb, 'active', now(), now() + interval '1 hour')",
        {"owner": owner_a},
    )
    assert _run("SELECT COUNT(*) FROM social_connections", scalar=True) == 3

    command.downgrade(cfg, "0002")

    legacy = _rows(
        "SELECT tenant_id, provider_user_id, encrypted_user_token FROM social_connections "
        "ORDER BY tenant_id"
    )
    # dedupe kept the newest row for (tenant-a, meta, u-a); tenant_id is the owner slug.
    assert legacy == [("tenant-a", "u-a", "gAAAAA-newest"), ("tenant-b", "u-b", "gAAAAA-b")]
    cols_after = {r[0] for r in _rows(columns_sql)}
    assert "tenant_id" in cols_after and "brand_id" not in cols_after
    assert _run("SELECT COUNT(*) FROM pg_tables WHERE tablename = 'tenants'", scalar=True) == 0
    assert (
        _run(
            "SELECT COUNT(*) FROM pg_constraint WHERE conname = 'uq_social_connections_identity'",
            scalar=True,
        )
        == 1
    )

    command.upgrade(cfg, "head")  # must succeed again on the downgraded data
    assert _run("SELECT version_num FROM alembic_version", scalar=True) == "0004"  # SPRINT-04
    assert _run("SELECT COUNT(*) FROM tenants", scalar=True) == 2
    _run(f"TRUNCATE {', '.join(TABLES)} CASCADE")


# ---------------------------------------------------------------- I-B3 ---
async def test_brand_delete_cascades_and_tenant_delete_restricted(
    seeded_session: AsyncSession, engine: AsyncEngine
) -> None:
    repo = PostgresConnectedAccountRepository(seeded_session)
    await repo.upsert(_account())
    await PostgresBrandAccessGrantRepository(seeded_session).add(
        BrandAccessGrant(
            brand_id=BRAND_ID,
            grantee_tenant_id=AGENCY_ID,
            role=GrantRole.MANAGER,
            granted_by_tenant_id=OWNER_ID,
        )
    )
    await seeded_session.commit()
    assert await _count(engine, "brand_access_grants") == 1

    with pytest.raises(IntegrityError):  # RESTRICT: owner still owns brands
        await seeded_session.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": OWNER_ID})
    await seeded_session.rollback()

    await seeded_session.execute(text("DELETE FROM brands WHERE id = :id"), {"id": BRAND_ID})
    await seeded_session.commit()
    assert await _count(engine, "social_connections") == 0
    assert await _count(engine, "social_connection_pages") == 0
    assert await _count(engine, "brand_access_grants") == 0
    assert await _count(engine, "brands") == 1  # BRAND2 untouched


# ---------------------------------------------------------------- I-B4 ---
async def test_grant_partial_unique_index_allows_regrant_after_revoke(
    seeded_session: AsyncSession,
) -> None:
    grants = PostgresBrandAccessGrantRepository(seeded_session)
    resolver = PostgresBrandAccessResolver(seeded_session)

    def _grant(role: GrantRole = GrantRole.MANAGER) -> BrandAccessGrant:
        return BrandAccessGrant(
            brand_id=BRAND_ID,
            grantee_tenant_id=AGENCY_ID,
            role=role,
            granted_by_tenant_id=OWNER_ID,
        )

    first = await grants.add(_grant())
    await seeded_session.commit()
    assert await resolver.resolve(AGENCY_ID, BRAND_ID) is BrandRole.MANAGER

    # H4: the partial unique index fires ⇒ domain 409, and the transaction stays usable
    # (no rollback needed — ON CONFLICT DO NOTHING is not an error for PostgreSQL).
    with pytest.raises(GrantAlreadyActiveError):
        await grants.add(_grant(GrantRole.VIEWER))

    assert await grants.revoke(BRAND_ID, first.id) is not None
    await seeded_session.commit()
    assert await resolver.resolve(AGENCY_ID, BRAND_ID) is None

    second = await grants.add(_grant(GrantRole.VIEWER))
    await seeded_session.commit()
    assert second.id != first.id
    assert await resolver.resolve(AGENCY_ID, BRAND_ID) is BrandRole.VIEWER
    assert await resolver.resolve(OWNER_ID, BRAND_ID) is BrandRole.OWNER
    assert await resolver.resolve(STRANGER_ID, BRAND_ID) is None
    assert await resolver.resolve(OWNER_ID, uuid4()) is None
    assert len(await grants.list_by_brand(BRAND_ID)) == 2
    accessible = await PostgresBrandRepository(seeded_session).list_accessible(AGENCY_ID)
    assert [(b.id, role) for b, role in accessible] == [(BRAND_ID, BrandRole.VIEWER)]


# ---------------------------------------------------------------- I-B7 ---
async def test_check_constraint_rejects_selected_page_without_token(
    seeded_session: AsyncSession,
) -> None:
    stored = await PostgresConnectedAccountRepository(seeded_session).upsert(_account(pages=[]))
    await seeded_session.commit()

    with pytest.raises(IntegrityError) as exc:
        await seeded_session.execute(
            text(
                "INSERT INTO social_connection_pages (id, connection_id, page_id, page_name, "
                "tasks, encrypted_page_token, is_selected) VALUES "
                "(:id, :cid, 'x', 'X', '[]'::jsonb, NULL, true)"
            ),
            {"id": uuid4(), "cid": stored.id},
        )
    assert "ck_social_connection_pages_selected_has_token" in str(exc.value)
    await seeded_session.rollback()


# ------------------------------------------------------------------ I8 ---
def test_full_callback_flow_persists_via_api(
    integration_app: FastAPI, graph_mock: respx.MockRouter
) -> None:
    """start → callback → GET — then a *new* app instance still sees the row."""
    _seed_fresh()
    _mock_graph(graph_mock, pages=2)
    owner = integration_headers(integration_app.state.settings, OWNER_ID)

    with TestClient(integration_app) as client:
        started = client.post(f"{brand_base()}/connections/start", headers=owner)
        assert started.status_code == 200, started.text
        state = started.json()["state"]
        assert SIGNER.verify(state) == (OWNER_ID, BRAND_ID)

        cb = client.get(CALLBACK, params={"code": "CODE", "state": state})
        assert cb.status_code == 201, cb.text
        created = cb.json()
        assert created["provider_user_id"] == "90001"
        assert created["brand_id"] == str(BRAND_ID)
        assert created["connected_by_tenant_id"] == str(OWNER_ID)
        assert "LONG_LIVED" not in cb.text and "PAGE_TOKEN" not in cb.text
        assert "token" not in cb.text.lower().replace("has_page_token", "")
        # Two pages ⇒ nothing selected yet (B6).
        assert all(p["is_selected"] is False for p in created["pages"])
        assert all(p["has_page_token"] is False for p in created["pages"])

        listed = client.get(f"{brand_base()}/connections", headers=owner)
        assert [a["id"] for a in listed.json()] == [created["id"]]
        assert client.get(f"{brand_base(BRAND2_ID)}/connections", headers=owner).json() == []
        stranger = client.get(
            f"{brand_base()}/connections",
            headers=integration_headers(integration_app.state.settings, STRANGER_ID),
        )
        assert stranger.status_code == 404
        assert stranger.json()["detail"]["error"] == "brand_not_found"

    # Simulate a server restart: brand-new application object + engine, same database.
    from src.main import create_app

    reset_db_caches()
    restarted = create_app(integration_app.state.settings)
    with TestClient(restarted) as client:
        again = client.get(f"{brand_base()}/connections/{created['id']}", headers=owner)
        assert again.status_code == 200
        pages = {p["page_id"]: p for p in again.json()["pages"]}
        assert pages["1001"]["instagram"]["username"] == "nilecoffee"

        gone = client.delete(f"{brand_base()}/connections/{created['id']}", headers=owner)
        assert gone.status_code == 204
        assert client.get(f"{brand_base()}/connections", headers=owner).json() == []

    assert _run("SELECT COUNT(*) FROM social_connections", scalar=True) == 0
    assert _run("SELECT COUNT(*) FROM social_connection_pages", scalar=True) == 0


# ---------------------------------------------------------------- I-B5 ---
def test_disconnect_leaves_no_provider_name_anywhere(
    integration_app: FastAPI, graph_mock: respx.MockRouter
) -> None:
    """I-B5 (B7) — the trusted ``/me`` name lives only in ``social_connections``."""
    _seed_fresh()
    _mock_graph(graph_mock, pages=1)
    # A name that appears nowhere in the seeded world, so the final scan is unambiguous.
    needle = "Zed Meta Person"
    graph_mock.get("/me", params__contains={"fields": "id,name"}).mock(
        return_value=httpx.Response(200, json=me_payload(user_id="90001", name=needle))
    )
    owner = integration_headers(integration_app.state.settings, OWNER_ID)

    with TestClient(integration_app) as client:
        state = client.post(f"{brand_base()}/connections/start", headers=owner).json()["state"]
        created = client.get(CALLBACK, params={"code": "CODE", "state": state}).json()
        assert created["provider_user_name"] == needle
        assert created["pages"][0]["is_selected"] is True  # single page auto-selected
        assert created["pages"][0]["has_page_token"] is True

    assert (
        _run(
            "SELECT COUNT(*) FROM social_connections WHERE provider_user_name = :needle",
            {"needle": needle},
            scalar=True,
        )
        == 1
    )
    with TestClient(integration_app) as client:
        res = client.delete(f"{brand_base()}/connections/{created['id']}", headers=owner)
        assert res.status_code == 204

    # Scan every text column of every public table for the name — it must be gone.
    text_columns = _rows(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND data_type IN ('text', 'character varying')"
    )
    assert text_columns
    for table, column in text_columns:
        hits = _run(
            f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" = :needle',  # noqa: S608
            {"needle": needle},
            scalar=True,
        )
        assert hits == 0, (table, column)
    assert _run("SELECT COUNT(*) FROM social_connection_pages", scalar=True) == 0


# ---------------------------------------------------------------- I-B6 ---
def test_revoke_grant_purges_agency_connections_via_api(
    integration_app: FastAPI, graph_mock: respx.MockRouter
) -> None:
    """I-B6 (B8) — revoking the agency's grant deletes its connections; the owner's remain."""
    _seed_fresh()
    _mock_graph(graph_mock, pages=1)
    owner = integration_headers(integration_app.state.settings, OWNER_ID)
    agency = integration_headers(integration_app.state.settings, AGENCY_ID)

    with TestClient(integration_app) as client:
        grant = client.post(
            f"{API}/brands/{BRAND_ID}/grants",
            json={"grantee_tenant_id": str(AGENCY_ID), "role": "manager"},
            headers=owner,
        )
        assert grant.status_code == 201, grant.text

        # Owner connects Meta user 90001; agency connects Meta user 90002.
        state = client.post(f"{brand_base()}/connections/start", headers=owner).json()["state"]
        owner_conn = client.get(CALLBACK, params={"code": "CODE", "state": state})
        assert owner_conn.status_code == 201, owner_conn.text

        graph_mock.get("/me", params__contains={"fields": "id,name"}).mock(
            return_value=httpx.Response(200, json=me_payload(user_id="90002", name="Agency"))
        )
        graph_mock.post("/debug_token").mock(
            return_value=httpx.Response(200, json=debug_token_payload(user_id="90002"))
        )
        state = client.post(f"{brand_base()}/connections/start", headers=agency).json()["state"]
        agency_conn = client.get(CALLBACK, params={"code": "CODE", "state": state})
        assert agency_conn.status_code == 201, agency_conn.text
        assert agency_conn.json()["connected_by_tenant_id"] == str(AGENCY_ID)

        assert len(client.get(f"{brand_base()}/connections", headers=owner).json()) == 2

        revoked = client.delete(
            f"{API}/brands/{BRAND_ID}/grants/{grant.json()['id']}", headers=owner
        )
        assert revoked.status_code == 204, revoked.text

        remaining = client.get(f"{brand_base()}/connections", headers=owner).json()
        assert [c["id"] for c in remaining] == [owner_conn.json()["id"]]
        assert remaining[0]["connected_by_tenant_id"] == str(OWNER_ID)

        # The agency has lost access entirely (404, not 403 — no information leak).
        assert client.get(f"{brand_base()}/connections", headers=agency).status_code == 404

    assert _run("SELECT COUNT(*) FROM social_connections", scalar=True) == 1
    assert (
        _run(
            "SELECT COUNT(*) FROM social_connections WHERE connected_by_tenant_id = :id",
            {"id": AGENCY_ID},
            scalar=True,
        )
        == 0
    )


# ---------------------------------------------------- SPRINT-04 I-B1 (ex-I-B8) ---
AUTH = f"{API}/auth"
CSRF = {"X-Requested-With": "XMLHttpRequest"}
COOKIE = "refresh"  # AUTH_COOKIE_SECURE=false in tests
PASSWORD = "a-strong-passphrase-42"  # noqa: S105 - test fixture value
SEED_PASSWORD = "correct-horse-battery"  # noqa: S105 - == tests.conftest.TEST_PASSWORD
LIVE_SESSIONS = "SELECT COUNT(*) FROM refresh_sessions WHERE user_id = :u AND revoked_at IS NULL"


def _register(client: TestClient, slug: str, email: str) -> tuple[str, str, str]:
    """``POST /auth/register`` ⇒ (tenant_id, user_id, bearer)."""
    res = client.post(
        f"{AUTH}/register",
        json={
            "email": email,
            "password": PASSWORD,
            "tenant_slug": slug,
            "tenant_name": slug.title(),
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    return body["user"]["tenant_id"], body["user"]["id"], f"Bearer {body['access_token']}"


def test_full_tenancy_flow_via_api_with_jwt(
    integration_app: FastAPI, graph_mock: respx.MockRouter
) -> None:
    """I-B1 — register owner + agency ⇒ brand ⇒ grant(manager) ⇒ start (agency) ⇒ callback
    (3 pages) ⇒ selection (1) ⇒ list ⇒ revoke grant ⇒ agency connections = 0 — all on PG,
    every protected call authenticated with a *real* JWT from ``/auth/register``."""
    _mock_graph(graph_mock, pages=3)

    with TestClient(integration_app) as client:
        owner_id, owner_user, owner = _register(client, "flow-owner", "owner@flow.example")
        agency_id, _, agency = _register(client, "flow-agency", "agency@flow.example")
        owner_h, agency_h = {"Authorization": owner}, {"Authorization": agency}
        assert owner_id != agency_id

        # I-A3: duplicate slug ⇒ 409 and no orphan user row.
        users_before = _run("SELECT COUNT(*) FROM users", scalar=True)
        dup = client.post(
            f"{AUTH}/register",
            json={
                "email": "third@flow.example",
                "password": PASSWORD,
                "tenant_slug": "flow-owner",
                "tenant_name": "Again",
            },
        )
        assert dup.status_code == 409 and dup.json()["detail"]["error"] == "tenant_slug_taken"
        assert _run("SELECT COUNT(*) FROM users", scalar=True) == users_before

        me = client.get(f"{AUTH}/me", headers=owner_h).json()
        assert me["id"] == owner_user and me["tenant"]["slug"] == "flow-owner"

        brand = client.post(
            f"{API}/brands", json={"slug": "flow-brand", "name": "Brand"}, headers=owner_h
        )
        assert brand.status_code == 201, brand.text
        brand_id = brand.json()["id"]
        assert brand.json()["owner_tenant_id"] == owner_id

        # Agency cannot see the brand before a grant (404).
        assert client.get(f"{API}/brands/{brand_id}", headers=agency_h).status_code == 404
        grant = client.post(
            f"{API}/brands/{brand_id}/grants",
            json={"grantee_tenant_id": agency_id, "role": "manager"},
            headers=owner_h,
        )
        assert grant.status_code == 201, grant.text
        mine = client.get(f"{API}/brands", headers=agency_h).json()
        assert [(b["id"], b["role"]) for b in mine] == [(brand_id, "manager")]

        base = brand_base(UUID(brand_id))
        started = client.post(f"{base}/connections/start", headers=agency_h)
        assert started.status_code == 200, started.text
        state = started.json()["state"]
        assert SIGNER.verify(state) == (UUID(agency_id), UUID(brand_id))
        assert str(SIGNER.decode_payload(state)["u"]) == me_agency_user(client, agency_h)

        cb = client.get(CALLBACK, params={"code": "CODE", "state": state})  # no Bearer (B3)
        assert cb.status_code == 201, cb.text
        conn = cb.json()
        cid = conn["id"]
        assert conn["connected_by_tenant_id"] == agency_id
        assert len(conn["pages"]) == 3
        assert all(not p["is_selected"] and not p["has_page_token"] for p in conn["pages"])

        bad = client.put(
            f"{base}/connections/{cid}/pages/selection",
            json={"page_ids": ["1002", "9999"]},
            headers=agency_h,
        )
        assert bad.status_code == 422
        assert bad.json()["detail"] == {"error": "unknown_page_ids", "page_ids": ["9999"]}

        selected = client.put(
            f"{base}/connections/{cid}/pages/selection",
            json={"page_ids": ["1002"]},
            headers=agency_h,
        )
        assert selected.status_code == 200, selected.text
        pages = {p["page_id"]: p for p in selected.json()["pages"]}
        assert pages["1002"]["is_selected"] is True and pages["1002"]["has_page_token"] is True
        assert pages["1001"]["is_selected"] is False and pages["1001"]["has_page_token"] is False
        assert "PAGE_TOKEN" not in selected.text

        listed = client.get(f"{base}/connections", headers=owner_h).json()
        assert [c["id"] for c in listed] == [cid]

        revoked = client.delete(
            f"{API}/brands/{brand_id}/grants/{grant.json()['id']}", headers=owner_h
        )
        assert revoked.status_code == 204, revoked.text
        assert client.get(f"{base}/connections", headers=owner_h).json() == []
        assert client.get(f"{base}/connections", headers=agency_h).status_code == 404

    assert _run("SELECT COUNT(*) FROM social_connection_pages", scalar=True) == 0
    assert _run("SELECT COUNT(*) FROM social_connections", scalar=True) == 0
    assert _run("SELECT COUNT(*) FROM brand_access_grants", scalar=True) == 1
    assert (
        _run(
            "SELECT COUNT(*) FROM social_connections WHERE connected_by_tenant_id = :id",
            {"id": UUID(agency_id)},
            scalar=True,
        )
        == 0
    )


def me_agency_user(client: TestClient, headers: dict[str, str]) -> str:
    return client.get(f"{AUTH}/me", headers=headers).json()["id"]


# ---------------------------------------------------------------- I-A1 ---
def test_alembic_0004_roundtrip_and_case_insensitive_email() -> None:
    """I-A1 — 0003 → 0004 → 0003 → 0004; ``uq_users_email_lower`` rejects ``A@x.com`` after
    ``a@x.com``."""
    cfg = alembic_config(TEST_DATABASE_URL)
    command.downgrade(cfg, "0003")
    assert _run("SELECT version_num FROM alembic_version", scalar=True) == "0003"
    tables = {r[0] for r in _rows("SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    assert not {"users", "refresh_sessions"} & tables
    command.upgrade(cfg, "head")
    assert _run("SELECT version_num FROM alembic_version", scalar=True) == "0004"
    tables = {r[0] for r in _rows("SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    assert {"users", "refresh_sessions"} <= tables
    indexes = {r[0] for r in _rows("SELECT indexname FROM pg_indexes WHERE tablename = 'users'")}
    assert {"uq_users_email_lower", "uq_users_tenant_id"} <= indexes

    _seed_fresh()
    tenant_x = uuid4()
    _run("INSERT INTO tenants (id, slug, name) VALUES (:id, 'case-x', 'X')", {"id": tenant_x})
    _run(
        "INSERT INTO users (id, tenant_id, email, password_hash) VALUES "
        "(:id, :tenant, 'a@x.com', '$argon2id$x')",
        {"id": uuid4(), "tenant": tenant_x},
    )
    tenant_y = uuid4()
    _run("INSERT INTO tenants (id, slug, name) VALUES (:id, 'case-y', 'Y')", {"id": tenant_y})
    with pytest.raises(IntegrityError) as exc:
        _run(
            "INSERT INTO users (id, tenant_id, email, password_hash) VALUES "
            "(:id, :tenant, 'A@x.com', '$argon2id$y')",
            {"id": uuid4(), "tenant": tenant_y},
        )
    assert "uq_users_email_lower" in str(exc.value)
    # One user per tenant (A2): a second user on tenant_x is refused too.
    with pytest.raises(IntegrityError) as exc2:
        _run(
            "INSERT INTO users (id, tenant_id, email, password_hash) VALUES "
            "(:id, :tenant, 'b@x.com', '$argon2id$z')",
            {"id": uuid4(), "tenant": tenant_x},
        )
    assert "uq_users_tenant_id" in str(exc2.value)
    _run("DELETE FROM users")
    _run("DELETE FROM tenants WHERE slug IN ('case-x', 'case-y')")


# ---------------------------------------------------------------- I-A2 ---
def _cookie(response: httpx.Response) -> str:
    from http.cookies import SimpleCookie

    jar = SimpleCookie()
    for raw in response.headers.get_list("set-cookie"):
        jar.load(raw)
    return jar[COOKIE].value


def test_register_me_refresh_reuse_login_logout_all_on_pg(integration_app: FastAPI) -> None:
    """I-A2 — register ⇒ me ⇒ refresh ⇒ reuse old ⇒ 401 + all revoked in DB ⇒ login ⇒
    logout-all ⇒ every session ``revoked_at IS NOT NULL``."""
    with TestClient(integration_app) as client:
        reg = client.post(
            f"{AUTH}/register",
            json={
                "email": "Owner@Flow.example",
                "password": PASSWORD,
                "tenant_slug": "ia2-owner",
                "tenant_name": "IA2",
            },
        )
        assert reg.status_code == 201, reg.text
        bearer = {"Authorization": f"Bearer {reg.json()['access_token']}"}
        user_id = UUID(reg.json()["user"]["id"])
        old_cookie = _cookie(reg)
        old_sid = UUID(old_cookie.partition(".")[0])

        me = client.get(f"{AUTH}/me", headers=bearer)
        assert me.status_code == 200 and me.json()["tenant"]["slug"] == "ia2-owner"

        client.cookies.set(COOKIE, old_cookie, path=f"{API}/auth")
        rotated = client.post(f"{AUTH}/refresh", headers=CSRF)
        assert rotated.status_code == 200, rotated.text
        new_cookie = _cookie(rotated)
        new_sid = UUID(new_cookie.partition(".")[0])
        assert new_sid != old_sid

    row = _rows(
        "SELECT revoked_at IS NOT NULL, replaced_by FROM refresh_sessions WHERE id = :id",
        {"id": old_sid},
    )
    assert row == [(True, new_sid)]

    with TestClient(integration_app) as client:
        # Reuse of the rotated-out cookie ⇒ theft suspected ⇒ everything revoked.
        client.cookies.set(COOKIE, old_cookie, path=f"{API}/auth")
        replay = client.post(f"{AUTH}/refresh", headers=CSRF)
        assert replay.status_code == 401
        assert replay.json()["detail"]["error"] == "refresh_reuse_detected"

    assert _run(LIVE_SESSIONS, {"u": user_id}, scalar=True) == 0

    with TestClient(integration_app) as client:
        # Login again (case-insensitive email) ⇒ two new sessions ⇒ logout-all kills both.
        login = client.post(
            f"{AUTH}/login",
            json={"email": "owner@flow.example", "password": "a-strong-passphrase-42"},
        )
        assert login.status_code == 200, login.text
        second = client.post(
            f"{AUTH}/login",
            json={"email": "OWNER@FLOW.EXAMPLE", "password": "a-strong-passphrase-42"},
        )
        assert second.status_code == 200
        assert (
            _run(
                "SELECT COUNT(*) FROM refresh_sessions WHERE user_id = :u AND revoked_at IS NULL",
                {"u": user_id},
                scalar=True,
            )
            == 2
        )
        out = client.post(
            f"{AUTH}/logout-all",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )
        assert out.status_code == 204

    total = _run(
        "SELECT COUNT(*) FROM refresh_sessions WHERE user_id = :u", {"u": user_id}, scalar=True
    )
    live = _run(
        "SELECT COUNT(*) FROM refresh_sessions WHERE user_id = :u AND revoked_at IS NULL",
        {"u": user_id},
        scalar=True,
    )
    assert total == 4 and live == 0  # register + refresh + 2 logins, all revoked
    assert _run(
        "SELECT last_login_at IS NOT NULL FROM users WHERE id = :u", {"u": user_id}, scalar=True
    )


# ---------------------------------------------------------------- I-A4 ---
def test_db_stores_argon2id_and_sha256_only(integration_app: FastAPI) -> None:
    """I-A4 — ``password_hash`` is Argon2id, ``token_hash`` ≠ cookie secret, and no column
    anywhere holds the raw password or the raw refresh secret."""
    password = PASSWORD
    with TestClient(integration_app) as client:
        reg = client.post(
            f"{AUTH}/register",
            json={
                "email": "ia4@flow.example",
                "password": password,
                "tenant_slug": "ia4-owner",
                "tenant_name": "IA4",
            },
        )
        assert reg.status_code == 201, reg.text
        cookie = _cookie(reg)
    sid_text, _, secret = cookie.partition(".")

    stored_hash = _run(
        "SELECT password_hash FROM users WHERE email = 'ia4@flow.example'", scalar=True
    )
    assert isinstance(stored_hash, str) and stored_hash.startswith("$argon2id$")
    assert password not in stored_hash
    token_hash = _run(
        "SELECT token_hash FROM refresh_sessions WHERE id = :id",
        {"id": UUID(sid_text)},
        scalar=True,
    )
    assert token_hash != secret and len(token_hash) == 64 and secret not in token_hash
    columns = {
        r[0]
        for r in _rows(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name IN ('users', 'refresh_sessions')"
        )
    }
    assert not any(c in {"password", "token", "secret", "refresh_token"} for c in columns)
    text_columns = _rows(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND data_type IN ('text', 'character varying')"
    )
    for table, column in text_columns:
        for needle in (password, secret):
            hits = _run(
                f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" = :needle',  # noqa: S608
                {"needle": needle},
                scalar=True,
            )
            assert hits == 0, (table, column)


# ---------------------------------------------------------------- I-B2 ---
def test_current_user_fresh_after_logout_on_pg(
    integration_app: FastAPI, graph_mock: respx.MockRouter
) -> None:
    """I-B2 — after ``logout`` the same JWT still reads (``CurrentUser``, stateless) but a
    destructive ``DELETE …/connections/{id}`` is refused with 401 ``session_revoked``."""
    _seed_fresh()
    _mock_graph(graph_mock, pages=1)
    with TestClient(integration_app) as client:
        login = client.post(
            f"{AUTH}/login",
            json={"email": "nile-owner@example.com", "password": "correct-horse-battery"},
        )
        assert login.status_code == 200, login.text
        owner = {"Authorization": f"Bearer {login.json()['access_token']}"}
        state = client.post(f"{brand_base()}/connections/start", headers=owner).json()["state"]
        cid = client.get(CALLBACK, params={"code": "CODE", "state": state}).json()["id"]

        # The client jar also carries the refresh cookie ⇒ A6 CSRF header is required.
        assert client.post(f"{AUTH}/logout", headers={**owner, **CSRF}).status_code == 204

        still_reads = client.get(f"{brand_base()}/connections/{cid}", headers=owner)
        assert still_reads.status_code == 200  # documented: valid until exp (≤ 15 min)
        denied = client.delete(f"{brand_base()}/connections/{cid}", headers=owner)
        assert denied.status_code == 401
        assert denied.json()["detail"]["error"] == "session_revoked"
        assert denied.headers["www-authenticate"] == "Bearer"
        # Grant revoke and page selection are guarded the same way.
        sel = client.put(
            f"{brand_base()}/connections/{cid}/pages/selection",
            json={"page_ids": ["1001"]},
            headers=owner,
        )
        assert sel.status_code == 401 and sel.json()["detail"]["error"] == "session_revoked"

    assert _run("SELECT COUNT(*) FROM social_connections", scalar=True) == 1  # nothing deleted


# ---------------------------------------------------------------- A-T3 ---
def test_health_ready_live_200(integration_app: FastAPI) -> None:
    """A-T3 (ex-H12) — readiness probe does a real ``engine.connect()`` + ``SELECT 1`` on PG."""
    with TestClient(integration_app) as client:
        res = client.get("/health/ready")
        assert res.status_code == 200, res.text
        assert res.json() == {"status": "ready", "database": "ok"}
        assert client.get("/health").status_code == 200


# ======================================================= SPRINT-04.1 Part A
def _grant_agency(client: TestClient, owner: dict[str, str], role: str = "manager") -> None:
    res = client.post(
        f"{API}/brands/{BRAND_ID}/grants",
        json={"grantee_tenant_id": str(AGENCY_ID), "role": role},
        headers=owner,
    )
    assert res.status_code == 201, res.text


def _connect(client: TestClient, headers: dict[str, str]) -> dict:
    state = client.post(f"{brand_base()}/connections/start", headers=headers).json()["state"]
    res = client.get(CALLBACK, params={"code": "CODE", "state": state})
    assert res.status_code == 201, res.text
    return res.json()


def test_h1_i1_agency_pages_isolated_from_brand_owner_on_pg(
    integration_app: FastAPI, graph_mock: respx.MockRouter
) -> None:
    """H1-I1 — agency with 3 client Pages connects brand A; the owner of A never sees the
    unselected ones and cannot hijack a selection; exactly one Page token ends up stored."""
    _seed_fresh()
    _mock_graph(graph_mock, pages=3, user_id="90002")
    settings = integration_app.state.settings
    owner = integration_headers(settings, OWNER_ID)
    agency = integration_headers(settings, AGENCY_ID)

    with TestClient(integration_app) as client:
        _grant_agency(client, owner)
        created = _connect(client, agency)
        cid = created["id"]
        assert created["connected_by_tenant_id"] == str(AGENCY_ID)
        assert created["is_connection_owner"] is True and len(created["pages"]) == 3

        # Owner sees 0 Pages before any selection.
        seen = client.get(f"{brand_base()}/connections/{cid}", headers=owner)
        assert seen.status_code == 200
        assert seen.json()["pages"] == [] and seen.json()["is_connection_owner"] is False
        assert "Giza Gym" not in seen.text and "Cairo Dental" not in seen.text

        # Agency selects one Page.
        sel = client.put(
            f"{brand_base()}/connections/{cid}/pages/selection",
            json={"page_ids": ["1002"]},
            headers=agency,
        )
        assert sel.status_code == 200, sel.text

        # Owner now sees exactly that Page.
        seen = client.get(f"{brand_base()}/connections/{cid}", headers=owner).json()
        assert [p["page_id"] for p in seen["pages"]] == ["1002"]
        listed = client.get(f"{brand_base()}/connections", headers=owner).json()
        assert [p["page_id"] for p in listed[0]["pages"]] == ["1002"]

        # Owner tries to select another Page ⇒ 403, nothing changes.
        hijack = client.put(
            f"{brand_base()}/connections/{cid}/pages/selection",
            json={"page_ids": ["1001", "1003"]},
            headers=owner,
        )
        assert hijack.status_code == 403, hijack.text
        assert hijack.json()["detail"]["error"] == "not_connection_owner"

    assert (
        _run(
            "SELECT count(*) FROM social_connection_pages "
            "WHERE connection_id = :cid AND encrypted_page_token IS NOT NULL",
            {"cid": UUID(cid)},
            scalar=True,
        )
        == 1
    )
    assert (
        _run(
            "SELECT page_id FROM social_connection_pages "
            "WHERE connection_id = :cid AND is_selected",
            {"cid": UUID(cid)},
            scalar=True,
        )
        == "1002"
    )


def _status_of(cid: str) -> object:
    return _run(
        "SELECT status FROM social_connections WHERE id = :cid", {"cid": UUID(cid)}, scalar=True
    )


def test_h3_i1_expired_token_during_selection_persists_reauth_required_on_pg(
    integration_app: FastAPI, graph_mock: respx.MockRouter
) -> None:
    """H3-I1 — 401 **and** ``status = 'reauth_required'`` survives the request rollback."""
    _seed_fresh()
    _mock_graph(graph_mock, pages=3)
    owner = integration_headers(integration_app.state.settings, OWNER_ID)

    with TestClient(integration_app) as client:
        cid = _connect(client, owner)["id"]
        assert (
            _run(
                "SELECT status FROM social_connections WHERE id = :cid",
                {"cid": UUID(cid)},
                scalar=True,
            )
            == "active"
        )
        graph_mock.get("/me/accounts").mock(
            return_value=httpx.Response(
                400,
                json={
                    "error": {
                        "message": "Error validating access token: Session has expired",
                        "type": "OAuthException",
                        "code": 190,
                        "error_subcode": 463,
                    }
                },
            )
        )
        res = client.put(
            f"{brand_base()}/connections/{cid}/pages/selection",
            json={"page_ids": ["1001"]},
            headers=owner,
        )
        assert res.status_code == 401, res.text
        assert res.json()["detail"]["required_action"] == "reconnect"

        # Visible through the API in a *new* request …
        again = client.get(f"{brand_base()}/connections/{cid}", headers=owner)
        assert again.json()["status"] == "reauth_required"

    # … and really committed in the database after the request is over.
    assert (
        _run(
            "SELECT status FROM social_connections WHERE id = :cid",
            {"cid": UUID(cid)},
            scalar=True,
        )
        == "reauth_required"
    )


def test_h4_i1_concurrent_inserts_with_same_slug_yield_one_201_and_one_409(
    engine: AsyncEngine,
) -> None:
    """H4-I1 — two sessions race on ``brands.uq_brands_owner_slug`` ⇒ 201 + 409, never 500."""
    del engine  # fixture used for its TRUNCATE side effect only
    _seed_fresh()
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from src.modules.tenancy.domain.models import Brand, Tenant
    from src.modules.tenancy.exceptions import BrandSlugTakenError, TenantSlugTakenError
    from src.modules.tenancy.infrastructure.postgres_repository import PostgresTenantRepository

    async def _race_brands() -> list[object]:
        eng = create_async_engine(TEST_DATABASE_URL)
        maker = async_sessionmaker(eng, expire_on_commit=False)

        async def _insert(name: str) -> object:
            async with maker() as session, session.begin():
                try:
                    brand = await PostgresBrandRepository(session).add(
                        Brand(owner_tenant_id=OWNER_ID, slug="raced-slug", name=name)
                    )
                except BrandSlugTakenError as exc:
                    return exc
                return brand

        try:
            # Both sessions open their transaction first, then insert "at the same time":
            # the loser blocks on the unique index until the winner commits, then its
            # ON CONFLICT DO NOTHING returns no row ⇒ domain 409, no IntegrityError.
            return list(await asyncio.gather(_insert("one"), _insert("two")))
        finally:
            await eng.dispose()

    results = asyncio.run(_race_brands())
    kinds = sorted(type(r).__name__ for r in results)
    assert kinds == ["Brand", "BrandSlugTakenError"], results
    assert not any(isinstance(r, IntegrityError) for r in results)
    assert _run("SELECT count(*) FROM brands WHERE slug = 'raced-slug'", scalar=True) == 1

    async def _race_tenants() -> list[object]:
        eng = create_async_engine(TEST_DATABASE_URL)
        maker = async_sessionmaker(eng, expire_on_commit=False)

        async def _insert(name: str) -> object:
            async with maker() as session, session.begin():
                try:
                    return await PostgresTenantRepository(session).add(
                        Tenant(slug="raced-tenant", name=name)
                    )
                except TenantSlugTakenError as exc:
                    return exc

        try:
            return list(await asyncio.gather(_insert("a"), _insert("b")))
        finally:
            await eng.dispose()

    results = asyncio.run(_race_tenants())
    assert sorted(type(r).__name__ for r in results) == ["Tenant", "TenantSlugTakenError"]


def test_h4_i1_concurrent_register_same_email_via_api_is_409_not_500(
    integration_app: FastAPI,
) -> None:
    """H4-I1 (API) — two sessions insert the same e-mail: one user row, one 409."""
    _seed_fresh()
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from src.modules.auth.domain.models import User
    from src.modules.auth.exceptions import EmailTakenError
    from src.modules.auth.infrastructure.postgres_repository import PostgresUserRepository

    async def _race() -> list[object]:
        eng = create_async_engine(TEST_DATABASE_URL)
        maker = async_sessionmaker(eng, expire_on_commit=False)

        async def _insert(tenant: UUID, email: str) -> object:
            async with maker() as session, session.begin():
                try:
                    return await PostgresUserRepository(session).add(
                        User(tenant_id=tenant, email=email, password_hash="$argon2id$x")
                    )
                except EmailTakenError as exc:
                    return exc

        try:
            # Different tenants (users.tenant_id is unique too), same e-mail in two cases.
            await asyncio.gather(
                *[
                    _run_sql(
                        eng,
                        "INSERT INTO tenants (id, slug, name) VALUES (:id, :s, :n)",
                        {"id": tid, "s": f"t-{i}", "n": f"T{i}"},
                    )
                    for i, tid in enumerate((t1, t2))
                ]
            )
            return list(
                await asyncio.gather(
                    _insert(t1, "Race@Example.com"), _insert(t2, "race@example.com")
                )
            )
        finally:
            await eng.dispose()

    t1, t2 = uuid4(), uuid4()
    results = asyncio.run(_race())
    assert sorted(type(r).__name__ for r in results) == ["EmailTakenError", "User"], results
    assert (
        _run("SELECT count(*) FROM users WHERE lower(email) = 'race@example.com'", scalar=True)
        == 1
    )


async def _run_sql(engine: AsyncEngine, sql: str, params: dict) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(sql), params)
