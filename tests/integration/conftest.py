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

pytestmark = pytest.mark.integration

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
if not TEST_DATABASE_URL:
    pytest.skip("integration DB not configured (set TEST_DATABASE_URL)", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[2]
TABLES = ("social_connection_pages", "social_connections")


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

    reset_db_caches()
    monkeypatch.setattr(database, "get_settings", lambda: integration_settings)
    yield create_app(integration_settings)
    reset_db_caches()


@pytest.fixture
def integration_encryption(encryption: TokenEncryptionService) -> TokenEncryptionService:
    return encryption
