"""PostgreSQL ``StatusMarker`` — one independent, committed transaction per call (H3)."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.database import get_session_factory
from src.modules.social_connect.domain.models import AccountStatus
from src.modules.social_connect.infrastructure.postgres_repository import (
    PostgresConnectedAccountRepository,
)

SessionFactoryProvider = Callable[[], async_sessionmaker[AsyncSession]]


class PostgresStatusMarker:
    def __init__(self, session_factory: SessionFactoryProvider = get_session_factory) -> None:
        self._session_factory = session_factory

    async def mark_out_of_band(
        self, brand_id: UUID, account_id: UUID, status: AccountStatus
    ) -> bool:
        async with self._session_factory()() as session, session.begin():
            return await PostgresConnectedAccountRepository(session).set_status(
                brand_id, account_id, status
            )
