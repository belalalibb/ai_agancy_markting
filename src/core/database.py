"""Database wiring (SQLAlchemy 2.x, PostgreSQL 16+ / pgvector — ARCHITECTURE_LOCK D-032).

Only the engine/session factory is defined here; no connection is opened at import
time so the application and the test-suite boot without a live database.
"""

from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from src.core.config import Settings, get_settings


class Base(DeclarativeBase):
    """Declarative base shared by all modules' ORM models."""


def build_engine(settings: Settings | None = None) -> Engine:
    """Create a SQLAlchemy engine from settings (lazy — no connection yet)."""
    settings = settings or get_settings()
    return create_engine(
        settings.database_url,
        echo=settings.database_echo,
        pool_pre_ping=True,
    )


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return build_engine()


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


def get_db_session() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a transactional session."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
