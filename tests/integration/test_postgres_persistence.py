"""Integration tests (G2.5) — real PostgreSQL: migrations, repository, full API flow.

Run with::

    TEST_DATABASE_URL=postgresql+asyncpg://aigos:aigos_dev_only@localhost:5432/ai_growth_os \
        pytest -m integration -q
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
import respx
from alembic import command
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
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
from tests.conftest import FAKE_STATE_SECRET, debug_token_payload, pages_payload, token_payload
from tests.integration.conftest import (
    TABLES,
    TEST_DATABASE_URL,
    alembic_config,
    reset_db_caches,
)

pytestmark = pytest.mark.integration

BASE = "/api/v1/social/meta"


def _account(
    tenant: str = "tenant-a",
    user: str = "u-1",
    *,
    pages: list[PageAsset] | None = None,
    token: str = "gAAAAA-cipher",  # noqa: S107 - fake ciphertext, not a secret
) -> ConnectedAccount:
    return ConnectedAccount(
        tenant_id=tenant,
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
                instagram=InstagramAsset(ig_user_id="178", username="nile", name="Nile Coffee"),
            ),
            PageAsset(page_id="1002", page_name="Cairo", encrypted_page_token="gAAAAA-page2"),
        ],
    )


async def _count(engine: AsyncEngine, table: str) -> int:
    assert table in TABLES  # constant, test-controlled identifiers only
    async with engine.connect() as conn:
        return (await conn.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar_one()  # noqa: S608


# ------------------------------------------------------------------ I1 ---
def test_alembic_upgrade_head_and_downgrade_base() -> None:
    """Round-trip the migration on the real database; ``vector`` must exist after upgrade."""
    import asyncio

    from sqlalchemy.ext.asyncio import create_async_engine

    cfg = alembic_config(TEST_DATABASE_URL)

    async def _tables() -> set[str]:
        eng = create_async_engine(TEST_DATABASE_URL)
        try:
            async with eng.connect() as conn:
                rows = await conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
                ext = await conn.execute(
                    text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
                )
                return {r[0] for r in rows} | {f"ext:{r[0]}" for r in ext}
        finally:
            await eng.dispose()

    command.downgrade(cfg, "base")
    after_down = asyncio.run(_tables())
    assert not set(TABLES) & after_down

    command.upgrade(cfg, "head")
    after_up = asyncio.run(_tables())
    assert set(TABLES) <= after_up
    assert "ext:vector" in after_up


# ------------------------------------------------------------------ I2 ---
async def test_repo_upsert_inserts_connection_and_pages(
    session: AsyncSession, engine: AsyncEngine
) -> None:
    repo: ConnectedAccountRepository = PostgresConnectedAccountRepository(session)
    stored = await repo.upsert(_account())
    await session.commit()

    assert stored.tenant_id == "tenant-a"
    assert stored.status is AccountStatus.ACTIVE
    assert [p.page_id for p in stored.pages] == ["1001", "1002"]
    assert stored.pages[0].instagram is not None
    assert stored.pages[0].instagram.username == "nile"
    assert stored.pages[0].tasks == ["ANALYZE", "MANAGE"]
    assert stored.pages[1].instagram is None
    assert stored.token_expires_at == datetime(2030, 1, 1, tzinfo=UTC)
    assert await _count(engine, "social_connections") == 1
    assert await _count(engine, "social_connection_pages") == 2


# ------------------------------------------------------------------ I3 ---
async def test_repo_upsert_same_provider_user_updates_not_duplicates(
    session: AsyncSession, engine: AsyncEngine
) -> None:
    repo = PostgresConnectedAccountRepository(session)
    first = await repo.upsert(_account(token="cipher-1"))
    await session.commit()

    replacement = _account(
        token="cipher-2",
        pages=[PageAsset(page_id="2001", page_name="New", encrypted_page_token="p")],
    )
    second = await repo.upsert(replacement)  # different ``id`` on purpose
    await session.commit()

    assert second.id == first.id  # the unique constraint collapsed the rows
    assert second.encrypted_user_token == "cipher-2"
    assert [p.page_id for p in second.pages] == ["2001"]  # pages replaced wholesale
    assert second.connected_at == first.connected_at  # original connect time kept
    assert second.updated_at >= first.updated_at
    assert await _count(engine, "social_connections") == 1
    assert await _count(engine, "social_connection_pages") == 1


# ------------------------------------------------------------------ I4 ---
async def test_repo_tenant_isolation(session: AsyncSession) -> None:
    repo = PostgresConnectedAccountRepository(session)
    a = await repo.upsert(_account("tenant-a", "same-user"))
    b = await repo.upsert(_account("tenant-b", "same-user"))  # same provider user, other tenant
    await session.commit()

    assert a.id != b.id
    assert [x.id for x in await repo.list_by_tenant("tenant-a")] == [a.id]
    assert [x.id for x in await repo.list_by_tenant("tenant-b")] == [b.id]
    assert await repo.get("tenant-a", b.id) is None
    assert await repo.delete("tenant-a", b.id) is False
    assert await repo.set_status("tenant-a", b.id, AccountStatus.REVOKED) is False
    assert (await repo.get("tenant-b", b.id)).status is AccountStatus.ACTIVE  # type: ignore[union-attr]
    assert await repo.list_by_tenant("tenant-c") == []


# ------------------------------------------------------------------ I5 ---
async def test_repo_delete_cascades_pages(session: AsyncSession, engine: AsyncEngine) -> None:
    repo = PostgresConnectedAccountRepository(session)
    stored = await repo.upsert(_account())
    await session.commit()
    assert await _count(engine, "social_connection_pages") == 2

    assert await repo.delete("tenant-a", stored.id) is True
    await session.commit()
    assert await _count(engine, "social_connections") == 0
    assert await _count(engine, "social_connection_pages") == 0
    assert await repo.delete("tenant-a", stored.id) is False
    assert await repo.get("tenant-a", uuid4()) is None


# ------------------------------------------------------------------ I6 ---
async def test_repo_set_status(session: AsyncSession) -> None:
    repo = PostgresConnectedAccountRepository(session)
    stored = await repo.upsert(_account())
    await session.commit()

    assert await repo.set_status("tenant-a", stored.id, AccountStatus.REAUTH_REQUIRED) is True
    await session.commit()
    fetched = await repo.get("tenant-a", stored.id)
    assert fetched is not None
    assert fetched.status is AccountStatus.REAUTH_REQUIRED
    assert fetched.updated_at >= stored.updated_at
    assert await repo.set_status("tenant-a", uuid4(), AccountStatus.REVOKED) is False


# ------------------------------------------------------------------ I7 ---
async def test_stored_tokens_are_ciphertext_and_decryptable(
    session: AsyncSession, engine: AsyncEngine, integration_encryption: TokenEncryptionService
) -> None:
    raw_user, raw_page = "EAAG-user-token-plaintext", "EAAG-page-token-plaintext"
    account = _account(
        token=integration_encryption.encrypt_token(raw_user),
        pages=[
            PageAsset(
                page_id="1",
                page_name="P",
                encrypted_page_token=integration_encryption.encrypt_token(raw_page),
            )
        ],
    )
    await PostgresConnectedAccountRepository(session).upsert(account)
    await session.commit()

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


# ------------------------------------------------------------------ I8 ---
def test_full_callback_flow_persists_via_api(
    integration_app: FastAPI, graph_mock: respx.MockRouter
) -> None:
    """start → callback → GET /connections — then a *new* app instance still sees the row."""
    graph_mock.get("/oauth/access_token", params={"grant_type": "fb_exchange_token"}).mock(
        return_value=httpx.Response(200, json=token_payload("LONG_LIVED", 5183944))
    )
    graph_mock.get("/oauth/access_token").mock(
        return_value=httpx.Response(200, json=token_payload("SHORT_LIVED", 5000))
    )
    graph_mock.get("/debug_token").mock(
        return_value=httpx.Response(200, json=debug_token_payload(user_id="90001"))
    )
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, json=pages_payload()))

    with TestClient(integration_app) as client:
        started = client.post(f"{BASE}/connections/start", json={"tenant_id": "demo"})
        assert started.status_code == 200
        state = started.json()["state"]
        assert OAuthStateSigner(FAKE_STATE_SECRET).verify(state) == "demo"

        cb = client.get(f"{BASE}/callback", params={"code": "CODE", "state": state})
        assert cb.status_code == 201, cb.text
        created = cb.json()
        assert created["provider_user_id"] == "90001"
        assert "LONG_LIVED" not in cb.text and "PAGE_TOKEN" not in cb.text
        assert "token" not in cb.text.lower()

        listed = client.get(f"{BASE}/connections", params={"tenant_id": "demo"})
        assert [a["id"] for a in listed.json()] == [created["id"]]
        assert client.get(f"{BASE}/connections", params={"tenant_id": "other"}).json() == []

    # Simulate a server restart: brand-new application object + engine, same database.
    from src.main import create_app

    reset_db_caches()
    restarted = create_app(integration_app.state.settings)
    with TestClient(restarted) as client:
        again = client.get(f"{BASE}/connections/{created['id']}", params={"tenant_id": "demo"})
        assert again.status_code == 200
        assert again.json()["pages"][0]["instagram"]["username"] == "nilecoffee"

        gone = client.delete(f"{BASE}/connections/{created['id']}", params={"tenant_id": "demo"})
        assert gone.status_code == 204
        assert client.get(f"{BASE}/connections", params={"tenant_id": "demo"}).json() == []

    # The TestClient's loop is closed; use a fresh loop + engine to assert the final state.
    import asyncio

    from sqlalchemy.ext.asyncio import create_async_engine

    async def _fresh_count() -> tuple[int, int]:
        eng = create_async_engine(TEST_DATABASE_URL)
        try:
            return (
                await _count(eng, "social_connections"),
                await _count(eng, "social_connection_pages"),
            )
        finally:
            await eng.dispose()

    assert asyncio.run(_fresh_count()) == (0, 0)
