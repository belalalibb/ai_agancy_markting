"""Async database wiring (SQLAlchemy 2.x + asyncpg, PostgreSQL 16+ — ARCHITECTURE_LOCK D-032).

Nothing connects at import time: the engine is created lazily on first use so the
application and the isolated test-suite boot without a live database.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from src.core.config import Settings, get_settings


class Base(DeclarativeBase):
    """Declarative base shared by all modules' ORM models."""


def build_engine(settings: Settings | None = None) -> AsyncEngine:
    """Create an async engine from settings (lazy — no connection is opened here)."""
    settings = settings or get_settings()
    return create_async_engine(
        settings.database_url,
        echo=settings.database_echo,
        pool_pre_ping=True,
    )


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    return build_engine()


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return build_session_factory(get_engine())


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one transactional session per request.

    Commits when the request handler returns normally, rolls back on any exception.
    """
    async with get_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
