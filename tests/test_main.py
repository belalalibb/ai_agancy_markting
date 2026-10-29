"""Unit tests — application lifespan and health probes (SPRINT-02.1 F6/F7).

Hermetic: no PostgreSQL. The engine is either never created (F6 "skip" path) or replaced
by a recording fake; ``/health/ready`` is exercised with a dependency override whose
session raises exactly what a dead database would.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from src.core import database
from src.core.config import Settings
from src.core.database import get_db_session
from src.main import create_app

pytestmark = pytest.mark.unit


class _FakeEngine:
    def __init__(self) -> None:
        self.disposed = 0

    async def dispose(self) -> None:
        self.disposed += 1


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


# ------------------------------------------------------------------ F7 (H12)
def _override_session(app: FastAPI, session: Any) -> None:
    async def _dep() -> AsyncIterator[Any]:
        yield session

    app.dependency_overrides[get_db_session] = _dep


class _DeadSession:
    async def execute(self, *_: object) -> None:
        raise OperationalError(
            "SELECT 1", {}, ConnectionRefusedError("postgresql://user:SECRET@db:5432/x")
        )


class _LiveSession:
    async def execute(self, *_: object) -> int:
        return 1


def test_health_ready_503_without_db(settings: Settings) -> None:
    app = create_app(settings)
    _override_session(app, _DeadSession())
    with TestClient(app) as client:
        res = client.get("/health/ready")
    assert res.status_code == 503
    assert res.json() == {"status": "not_ready", "database": "unreachable"}
    # The body must never leak the DSN / driver error (F7).
    assert "SECRET" not in res.text
    assert "postgresql" not in res.text
    assert "5432" not in res.text


def test_health_ready_200_with_mocked_session(settings: Settings) -> None:
    app = create_app(settings)
    _override_session(app, _LiveSession())
    with TestClient(app) as client:
        res = client.get("/health/ready")
    assert res.status_code == 200
    assert res.json() == {"status": "ready", "database": "ok"}


def test_health_ready_503_on_os_error(settings: Settings) -> None:
    class _Refused:
        async def execute(self, *_: object) -> None:
            raise ConnectionRefusedError("db down")

    app = create_app(settings)
    _override_session(app, _Refused())
    with TestClient(app) as client:
        res = client.get("/health/ready")
    assert res.status_code == 503
    assert res.json()["database"] == "unreachable"


def test_health_liveness_unchanged(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        body = client.get("/health").json()
    assert body == {"status": "ok", "app": settings.app_name, "env": settings.app_env}
