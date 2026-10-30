"""Unit tests — application lifespan and health probes (SPRINT-02.1 F6/F7, SPRINT-03 A6).

Hermetic: no PostgreSQL. The engine is either never created (F6 "skip" path) or replaced
by a fake via ``monkeypatch`` on ``database.get_engine``; ``/health/ready`` opens a raw
``engine.connect()`` — no ORM session, no transaction (A6).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from src.core import database
from src.core.config import Settings
from src.core.database import get_db_session
from src.main import create_app

pytestmark = pytest.mark.unit


class _FakeEngine:
    """Minimal stand-in for ``AsyncEngine``: ``connect()`` is an async context manager."""

    def __init__(self, failure: Exception | None = None) -> None:
        self.disposed = 0
        self.failure = failure
        self.executed: list[str] = []

    async def dispose(self) -> None:
        self.disposed += 1

    @asynccontextmanager
    async def connect(self) -> Any:
        engine = self

        class _Conn:
            async def execute(self, stmt: object) -> int:
                if engine.failure is not None:
                    raise engine.failure
                engine.executed.append(str(stmt))
                return 1

        yield _Conn()


@pytest.fixture(autouse=True)
def _fresh_engine_cache() -> Iterator[None]:
    database.get_engine.cache_clear()
    database.get_session_factory.cache_clear()
    yield
    database.get_engine.cache_clear()
    database.get_session_factory.cache_clear()


# ------------------------------------------------------------------ F6 (H11)
def test_lifespan_skips_dispose_when_engine_never_created(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: list[str] = []
    monkeypatch.setattr(database, "build_engine", lambda *_a, **_k: created.append("x"))

    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
    # No request ever touched the DB → the cache is still empty → nothing was built/disposed.
    assert database.get_engine.cache_info().currsize == 0
    assert created == []


def test_lifespan_disposes_engine_when_created(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeEngine()
    monkeypatch.setattr(database, "build_engine", lambda *_a, **_k: fake)

    with TestClient(create_app(settings)):
        # Simulate the first DB-backed request: the lru_cache now holds the engine.
        assert database.get_engine() is fake
        assert database.get_engine.cache_info().currsize == 1
        assert fake.disposed == 0
    assert fake.disposed == 1


# ------------------------------------------------------------------ A6 / F7 (A-T1)
def _patch_engine(monkeypatch: pytest.MonkeyPatch, engine: _FakeEngine) -> None:
    # The route calls ``database.get_engine()`` at request time; the ``lru_cache`` wrapper
    # must stay intact (lifespan inspects ``cache_info``), so the fake goes in via
    # ``build_engine`` and is cached on first use exactly like the real engine.
    monkeypatch.setattr(database, "build_engine", lambda *_a, **_k: engine)


def test_health_ready_200_with_fake_engine(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _FakeEngine()
    _patch_engine(monkeypatch, engine)
    with TestClient(create_app(settings)) as client:
        res = client.get("/health/ready")
    assert res.status_code == 200
    assert res.json() == {"status": "ready", "database": "ok"}
    assert engine.executed == ["SELECT 1"]


def test_health_ready_503_on_sqlalchemy_error(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    dead = OperationalError(
        "SELECT 1", {}, ConnectionRefusedError("postgresql://user:SECRET@db:5432/x")
    )
    _patch_engine(monkeypatch, _FakeEngine(failure=dead))
    with TestClient(create_app(settings)) as client:
        res = client.get("/health/ready")
    assert res.status_code == 503
    assert res.json() == {"status": "not_ready", "database": "unreachable"}
    # The body must never leak the DSN / driver error (F7).
    assert "SECRET" not in res.text
    assert "postgresql" not in res.text
    assert "5432" not in res.text


def test_health_ready_503_on_os_error(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_engine(monkeypatch, _FakeEngine(failure=ConnectionRefusedError("db down")))
    with TestClient(create_app(settings)) as client:
        res = client.get("/health/ready")
    assert res.status_code == 503
    assert res.json()["database"] == "unreachable"


# ------------------------------------------------------------------ A-T2
def test_health_ready_has_no_session_dependency(settings: Settings) -> None:
    app = create_app(settings)
    routes = [r for r in app.routes if isinstance(r, APIRoute) and r.path == "/health/ready"]
    assert len(routes) == 1
    route = routes[0]
    dependency_calls = [d.call for d in route.dependant.dependencies]
    assert get_db_session not in dependency_calls
    assert route.dependant.dependencies == []  # no DI at all on the readiness probe


def test_health_liveness_unchanged(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        body = client.get("/health").json()
    assert body == {"status": "ok", "app": settings.app_name, "env": settings.app_env}
