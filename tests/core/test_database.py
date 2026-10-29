"""Unit tests — database wiring (engine factory + transactional session dependency).

An in-memory SQLite URL is used *only* to exercise the SQLAlchemy plumbing without a
live server; production remains PostgreSQL 16+ per ARCHITECTURE_LOCK D-032.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, make_url, text
from sqlalchemy.orm import Mapped, mapped_column
from src.core import database
from src.core.config import Settings
from src.core.database import Base, build_engine, get_db_session

pytestmark = pytest.mark.unit


class _Probe(Base):
    __tablename__ = "probe"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]


@pytest.fixture
def sqlite_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"database_url": "sqlite+pysqlite:///:memory:"})


@pytest.fixture
def engine(sqlite_settings: Settings) -> Engine:
    eng = build_engine(sqlite_settings)
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture(autouse=True)
def _isolate_caches(monkeypatch: pytest.MonkeyPatch, engine: Engine) -> None:
    """Point the cached factories at the in-memory engine for the duration of a test."""
    database.get_engine.cache_clear()
    database.get_session_factory.cache_clear()
    monkeypatch.setattr(database, "get_engine", lambda: engine)


def test_build_engine_is_lazy_and_uses_settings_url(sqlite_settings: Settings) -> None:
    eng = build_engine(sqlite_settings)
    assert eng.url.get_backend_name() == "sqlite"
    assert eng.url.database == ":memory:"
    assert eng.echo is False


def test_default_settings_target_postgresql(settings: Settings) -> None:
    """ARCHITECTURE_LOCK D-032: the default DSN must be PostgreSQL (driver not required here)."""
    url = make_url(settings.database_url)
    assert url.get_backend_name() == "postgresql"
    # SPRINT-02 B2: the driver is now asyncpg (was psycopg in SPRINT-01).
    assert url.get_driver_name() == "asyncpg"


def test_session_dependency_commits_on_success(engine: Engine) -> None:
    gen = get_db_session()
    session = next(gen)
    session.add(_Probe(id=1, name="ok"))
    with pytest.raises(StopIteration):
        next(gen)  # triggers commit + close

    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM probe")).scalar() == 1


def test_session_dependency_rolls_back_on_error(engine: Engine) -> None:
    gen = get_db_session()
    session = next(gen)
    session.add(_Probe(id=2, name="boom"))
    session.flush()
    with pytest.raises(RuntimeError, match="handler failed"):
        gen.throw(RuntimeError("handler failed"))

    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM probe WHERE id = 2")).scalar() == 0


def test_session_factory_is_cached() -> None:
    assert database.get_session_factory() is database.get_session_factory()
