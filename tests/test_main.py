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


# ------------------------------------------------------------ SPRINT-07 B6-U1 scheduler loop
def test_b6_u1_scheduler_loop_not_started_when_disabled(settings: Settings) -> None:
    assert settings.scheduler_enabled is False
    with TestClient(create_app(settings)) as client:
        assert client.app.state.scheduler_task is None  # type: ignore[attr-defined]


def test_b6_u1_scheduler_loop_started_and_cancelled_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from src import main as main_module

    calls: list[str] = []

    async def fake_loop(scheduler: Any, poll_seconds: float) -> None:
        calls.append(type(scheduler).__name__)
        assert poll_seconds == 1
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            calls.append("cancelled")
            raise

    monkeypatch.setattr(main_module, "scheduler_loop", fake_loop)
    enabled = Settings(_env_file=None, scheduler_enabled=True, scheduler_poll_seconds=1)  # type: ignore[call-arg]
    with TestClient(create_app(enabled)) as client:
        task = client.app.state.scheduler_task  # type: ignore[attr-defined]
        assert task is not None and not task.done()
        assert client.get("/health").status_code == 200
    assert task.cancelled() or task.done()
    assert calls == ["PostgresScheduler", "cancelled"]


# ------------------------------------------------------------ SPRINT-08 A-1 encryption guard
def test_a1_u1_scheduler_enabled_without_encryption_key_refuses_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A-1: ``SCHEDULER_ENABLED=true`` + no ``TOKEN_ENCRYPTION_KEY`` ⇒ ``lifespan`` raises
    ``RuntimeError`` *before* any scheduler object is built (no loop, no silent failures)."""
    from src import main as main_module
    from src.modules.publishing.scheduler import SCHEDULER_REQUIRES_ENCRYPTION_KEY

    built: list[str] = []
    monkeypatch.setattr(
        main_module,
        "PostgresScheduler",
        lambda *a, **k: built.append("scheduler"),  # must never be reached
    )
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    broken = Settings(_env_file=None, scheduler_enabled=True, token_encryption_key=None)  # type: ignore[call-arg]
    assert broken.token_encryption_key is None
    with pytest.raises(RuntimeError, match="SCHEDULER_ENABLED requires TOKEN_ENCRYPTION_KEY"):
        with TestClient(create_app(broken)):
            pass  # pragma: no cover - lifespan must fail before serving
    assert SCHEDULER_REQUIRES_ENCRYPTION_KEY == "SCHEDULER_ENABLED requires TOKEN_ENCRYPTION_KEY"
    assert built == []


def test_a1_u1_postgres_scheduler_rejects_encryption_none(settings: Settings) -> None:
    """A-1 second layer: ``PostgresScheduler.__init__`` refuses ``encryption=None``."""
    from src.modules.publishing.scheduler import PostgresScheduler
    from src.modules.social_connect.infrastructure.status_marker import PostgresStatusMarker

    from tests.fakes import FakePublisher

    with pytest.raises(RuntimeError, match="SCHEDULER_ENABLED requires TOKEN_ENCRYPTION_KEY"):
        PostgresScheduler(
            database.get_session_factory,
            lambda _channel: FakePublisher(),  # SPRINT-08 B-1: PublisherResolver
            PostgresStatusMarker(),
            settings,
            encryption=None,
        )


def test_a1_u2_scheduler_enabled_with_encryption_key_creates_task(
    monkeypatch: pytest.MonkeyPatch, fernet_key: str
) -> None:
    """A-1: with a key the task is created (complements B6-U1, which already passes a key
    through the environment); the task is cancelled on shutdown."""
    import asyncio

    from src import main as main_module

    async def fake_loop(scheduler: Any, poll_seconds: float) -> None:
        await asyncio.sleep(3600)  # cancelled by lifespan shutdown

    monkeypatch.setattr(main_module, "scheduler_loop", fake_loop)
    enabled = Settings(_env_file=None, scheduler_enabled=True, token_encryption_key=fernet_key)  # type: ignore[call-arg]
    with TestClient(create_app(enabled)) as client:
        task = client.app.state.scheduler_task  # type: ignore[attr-defined]
        assert task is not None and not task.done()
    assert task.cancelled() or task.done()
