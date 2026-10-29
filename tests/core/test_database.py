"""Unit tests — async database wiring (engine factory + transactional session dependency).

SPRINT-02 (D-032): the SPRINT-01 version exercised the plumbing with an in-memory SQLite
URL; SQLite is now forbidden even in tests, so the engine is built lazily against the
PostgreSQL URL (no connection is ever opened here) and the session dependency is
exercised with a fake session. Real round-trips live in ``tests/integration``.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from src.core import database
from src.core.config import Settings
from src.core.database import Base, build_engine, build_session_factory, get_db_session

pytestmark = pytest.mark.unit


class _FakeSession:
    """Records the transaction calls ``get_db_session`` is expected to make."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __aenter__(self) -> _FakeSession:
        self.calls.append("enter")
        return self

    async def __aexit__(self, *_: object) -> None:
        self.calls.append("close")

    async def commit(self) -> None:
        self.calls.append("commit")

    async def rollback(self) -> None:
        self.calls.append("rollback")


@pytest.fixture
def fake_session(monkeypatch: pytest.MonkeyPatch) -> _FakeSession:
    session = _FakeSession()
    database.get_engine.cache_clear()
    database.get_session_factory.cache_clear()
    monkeypatch.setattr(database, "get_session_factory", lambda: lambda: session)
    return session


def test_build_engine_is_lazy_and_uses_settings_url(settings: Settings) -> None:
    eng = build_engine(settings)
    assert isinstance(eng, AsyncEngine)
    assert eng.url.get_backend_name() == "postgresql"
    assert eng.url.get_driver_name() == "asyncpg"
    assert eng.echo is False
    assert eng.pool.checkedout() == 0  # nothing connected yet


def test_default_settings_target_postgresql(settings: Settings) -> None:
    """ARCHITECTURE_LOCK D-032: the default DSN must be PostgreSQL via asyncpg."""
    url = make_url(settings.database_url)
    assert url.get_backend_name() == "postgresql"
    assert url.get_driver_name() == "asyncpg"


def test_session_factory_configuration(settings: Settings) -> None:
    factory = build_session_factory(build_engine(settings))
    assert isinstance(factory, async_sessionmaker)
    kw: dict[str, Any] = factory.kw
    assert kw["expire_on_commit"] is False
    assert kw["autoflush"] is False
    assert factory.class_ is AsyncSession


def test_declarative_base_is_shared() -> None:
    from src.modules.social_connect.infrastructure.orm import SocialConnectionRow

    assert SocialConnectionRow.metadata is Base.metadata
    assert "social_connections" in Base.metadata.tables
    assert "social_connection_pages" in Base.metadata.tables


async def test_session_dependency_commits_on_success(fake_session: _FakeSession) -> None:
    gen = get_db_session()
    session = await anext(gen)
    assert session is fake_session
    with pytest.raises(StopAsyncIteration):
        await anext(gen)
    assert fake_session.calls == ["enter", "commit", "close"]


async def test_session_dependency_rolls_back_on_error(fake_session: _FakeSession) -> None:
    gen = get_db_session()
    await anext(gen)
    with pytest.raises(RuntimeError, match="handler failed"):
        await gen.athrow(RuntimeError("handler failed"))
    assert fake_session.calls == ["enter", "rollback", "close"]


def test_engine_and_factory_are_cached() -> None:
    database.get_engine.cache_clear()
    database.get_session_factory.cache_clear()
    try:
        assert database.get_engine() is database.get_engine()
        assert database.get_session_factory() is database.get_session_factory()
    finally:
        database.get_engine.cache_clear()
        database.get_session_factory.cache_clear()
