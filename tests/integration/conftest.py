"""Integration fixtures (G2.5) — a *real* PostgreSQL reached through ``TEST_DATABASE_URL``.

Without ``TEST_DATABASE_URL`` the whole package is skipped. With it:

* ``alembic upgrade head`` runs once per session (against that URL),
* every test starts from empty tables (``TRUNCATE … CASCADE``),
* the app fixture is wired to the real repository (no overrides).

No SQLite anywhere (ARCHITECTURE_LOCK D-032).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from src.core import database
from src.core.config import Settings
from src.core.database import build_engine, build_session_factory
from src.core.security import TokenEncryptionService
from src.modules.auth.domain.models import AuthenticatedActor
from src.modules.auth.infrastructure.argon2_hasher import Argon2PasswordHasher
from src.modules.auth.jwt import AccessTokenCodec

pytestmark = pytest.mark.integration

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
if not TEST_DATABASE_URL:
    pytest.skip("integration DB not configured (set TEST_DATABASE_URL)", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[2]
# Truncation order is irrelevant with CASCADE, but listed children-first for readability.
TABLES = (
    "approval_events",
    "content_revisions",
    "content_items",
    "refresh_sessions",
    "users",
    "social_connection_pages",
    "social_connections",
    "brand_access_grants",
    "brands",
    "tenants",
)


def alembic_config(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"database_url={url}"]})()  # -x database_url=…
    cfg.attributes["configure_logger"] = False  # keep app loggers alive for caplog tests
    return cfg


@pytest.fixture(scope="session", autouse=True)
def _migrate_once() -> Iterator[None]:
    """Bring the test database to ``head`` exactly once per session."""
    cfg = alembic_config(TEST_DATABASE_URL)
    command.upgrade(cfg, "head")
    yield


@pytest.fixture
def integration_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"database_url": TEST_DATABASE_URL})


@pytest.fixture
async def engine(integration_settings: Settings) -> AsyncIterator[AsyncEngine]:
    eng = build_engine(integration_settings)
    try:
        async with eng.begin() as conn:
            await conn.execute(text(f"TRUNCATE {', '.join(TABLES)} CASCADE"))
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return build_session_factory(engine)


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as s:
        yield s


def reset_db_caches() -> None:
    """Drop the cached engine/session factory (used to simulate a server restart)."""
    database.get_engine.cache_clear()
    database.get_session_factory.cache_clear()


@pytest.fixture
def integration_app(
    integration_settings: Settings,
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[FastAPI]:
    """Real app, real repository.

    The app must create its *own* engine lazily inside the ``TestClient`` event loop —
    asyncpg connections are loop-bound, so sharing the pytest-asyncio ``engine`` fixture
    (used here only for the TRUNCATE) would fail with "attached to a different loop".
    ``get_settings`` is patched so the lazily-built engine targets ``TEST_DATABASE_URL``.
    """
    from src.main import create_app
    from src.modules.auth.dependencies import get_rate_limiter
    from tests.fakes import FakeRateLimiter

    reset_db_caches()
    monkeypatch.setattr(database, "get_settings", lambda: integration_settings)
    application = create_app(integration_settings)
    # A8: the process-wide in-memory limiter would accumulate "testclient" hits across
    # tests; the directive mandates disabling it in tests through the fake dependency.
    application.dependency_overrides[get_rate_limiter] = FakeRateLimiter
    yield application
    reset_db_caches()


@pytest.fixture
def integration_encryption(encryption: TokenEncryptionService) -> TokenEncryptionService:
    return encryption


# ------------------------------------------------------------- tenancy seed --
# SPRINT-04: one user + one live refresh session per fixed-world tenant, so API-level
# integration tests can present a real Bearer JWT (``CurrentUser``) that also passes the
# ``CurrentUserFresh`` revocation check.
SESSION_BY_TENANT: dict[UUID, UUID] = {}
_SEED_HASH: str | None = None


def seed_password_hash() -> str:
    """Argon2id hash of ``tests.conftest.TEST_PASSWORD`` (computed once per session)."""
    global _SEED_HASH  # noqa: PLW0603 - memoised, hashing is deliberately slow
    if _SEED_HASH is None:
        from tests.conftest import TEST_PASSWORD

        _SEED_HASH = Argon2PasswordHasher().hash(TEST_PASSWORD)
    return _SEED_HASH


def integration_headers(settings: Settings, tenant_id: UUID) -> dict[str, str]:
    """Bearer headers for the seeded user of ``tenant_id`` (sid = seeded live session)."""
    from tests.conftest import USER_BY_TENANT

    assert settings.auth_jwt_secret is not None
    codec = AccessTokenCodec(settings.auth_jwt_secret, issuer=settings.app_name, ttl_seconds=900)
    actor = AuthenticatedActor(
        user_id=USER_BY_TENANT[tenant_id],
        tenant_id=tenant_id,
        sid=SESSION_BY_TENANT[tenant_id],
    )
    return {"Authorization": f"Bearer {codec.issue(actor)}"}


async def seed_tenancy(session: AsyncSession) -> None:
    """Insert the SPRINT-03 test world (same ids as ``tests.conftest``) and commit.

    tenants: OWNER (nile-owner), AGENCY (agency), STRANGER (stranger);
    brands : BRAND (nile-coffee) and BRAND2 (cairo-dental), both owned by OWNER;
    users  : one per tenant (``<slug>@example.com`` / ``TEST_PASSWORD``) + one live session.
    """
    from tests.conftest import (
        AGENCY_ID,
        BRAND2_ID,
        BRAND_ID,
        OWNER_ID,
        STRANGER_ID,
        USER_BY_TENANT,
    )

    await session.execute(
        text(
            "INSERT INTO tenants (id, slug, name) VALUES "
            "(:owner, 'nile-owner', 'Nile Owner'), "
            "(:agency, 'agency', 'Agency'), "
            "(:stranger, 'stranger', 'Stranger')"
        ),
        {"owner": OWNER_ID, "agency": AGENCY_ID, "stranger": STRANGER_ID},
    )
    await session.execute(
        text(
            "INSERT INTO brands (id, owner_tenant_id, slug, name) VALUES "
            "(:b1, :owner, 'nile-coffee', 'Nile Coffee'), "
            "(:b2, :owner, 'cairo-dental', 'Cairo Dental')"
        ),
        {"b1": BRAND_ID, "b2": BRAND2_ID, "owner": OWNER_ID},
    )
    slugs = {OWNER_ID: "nile-owner", AGENCY_ID: "agency", STRANGER_ID: "stranger"}
    for tenant_id, user_id in USER_BY_TENANT.items():
        sid = SESSION_BY_TENANT.setdefault(tenant_id, UUID(int=user_id.int + 0x1000))
        await session.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, password_hash) "
                "VALUES (:id, :tenant, :email, :hash)"
            ),
            {
                "id": user_id,
                "tenant": tenant_id,
                "email": f"{slugs[tenant_id]}@example.com",
                "hash": seed_password_hash(),
            },
        )
        await session.execute(
            text(
                "INSERT INTO refresh_sessions (id, user_id, token_hash, expires_at) "
                "VALUES (:id, :user, :hash, now() + interval '30 days')"
            ),
            {"id": sid, "user": user_id, "hash": "0" * 64},
        )
    await session.commit()


@pytest.fixture
async def seeded_session(session: AsyncSession) -> AsyncSession:
    await seed_tenancy(session)
    return session
