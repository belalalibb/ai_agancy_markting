"""A-2 DB kill switches — ``platform_flags`` + ``brands.suspended_at``.

``platform_readonly`` / ``feature_pulse`` / ``feature_lead_loop`` are read from the DB
**first**; a missing row falls back to ``Settings`` (no behaviour change until an admin flips
one). Every reader caches for 30 s inside the process (``FlagCache``) so the guard costs one
SQL statement per 30 s, not one per request.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Boolean, String, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from src.core.config import Settings
from src.core.database import Base

FLAG_KEYS: tuple[str, ...] = ("platform_readonly", "feature_pulse", "feature_lead_loop")
CACHE_SECONDS = 30
READONLY_RETRY_AFTER = 300


class PlatformFlag(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    value: bool
    updated_at: datetime


class PlatformFlagRow(Base):
    __tablename__ = "platform_flags"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class FlagRepository(Protocol):
    async def all_flags(self) -> dict[str, bool]: ...

    async def set_flag(self, key: str, value: bool, *, now: datetime) -> PlatformFlag: ...

    async def brand_suspended_at(self, brand_id: uuid.UUID) -> datetime | None | bool:
        """``False`` ⇒ brand unknown; ``None`` ⇒ active; datetime ⇒ suspended since."""
        ...

    async def set_brand_suspended(
        self, brand_id: uuid.UUID, suspended_at: datetime | None
    ) -> bool: ...


_FLAGS = text("SELECT key, value FROM platform_flags")
_UPSERT = text(
    "INSERT INTO platform_flags (key, value, updated_at) VALUES (:key, :value, :now) "
    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at "
    "RETURNING key, value, updated_at"
)
_SUSPENDED = text("SELECT suspended_at FROM brands WHERE id = :brand_id")
_SET_SUSPENDED = text(
    "UPDATE brands SET suspended_at = :suspended_at WHERE id = :brand_id RETURNING id"
)


class PgFlagRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def all_flags(self) -> dict[str, bool]:
        rows = await self._session.execute(_FLAGS)
        return {str(k): bool(v) for k, v in rows.all()}

    async def set_flag(self, key: str, value: bool, *, now: datetime) -> PlatformFlag:
        row = (
            await self._session.execute(_UPSERT, {"key": key, "value": value, "now": now})
        ).one()
        return PlatformFlag(key=row[0], value=bool(row[1]), updated_at=row[2])

    async def brand_suspended_at(self, brand_id: uuid.UUID) -> datetime | None | bool:
        row = (await self._session.execute(_SUSPENDED, {"brand_id": brand_id})).first()
        if row is None:
            return False
        return row[0]

    async def set_brand_suspended(
        self, brand_id: uuid.UUID, suspended_at: datetime | None
    ) -> bool:
        row = (
            await self._session.execute(
                _SET_SUSPENDED, {"brand_id": brand_id, "suspended_at": suspended_at}
            )
        ).first()
        return row is not None


class FlagCache:
    """Process-wide 30 s cache of the flag table (one per app; cleared on every admin write)."""

    def __init__(self, *, ttl_seconds: float = CACHE_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._flags: dict[str, bool] = {}
        self._loaded_at: float | None = None
        self._suspended: dict[uuid.UUID, tuple[float, datetime | None | bool]] = {}

    def invalidate(self) -> None:
        self._loaded_at = None
        self._suspended.clear()

    def _fresh(self, stamp: float | None) -> bool:
        return stamp is not None and (time.monotonic() - stamp) < self._ttl

    async def flags(self, repo: FlagRepository) -> dict[str, bool]:
        if not self._fresh(self._loaded_at):
            self._flags = await repo.all_flags()
            self._loaded_at = time.monotonic()
        return dict(self._flags)

    async def suspended_at(
        self, repo: FlagRepository, brand_id: uuid.UUID
    ) -> datetime | None | bool:
        hit = self._suspended.get(brand_id)
        if hit is not None and self._fresh(hit[0]):
            return hit[1]
        value = await repo.brand_suspended_at(brand_id)
        self._suspended[brand_id] = (time.monotonic(), value)
        return value


def effective(flags: dict[str, bool], settings: Settings, key: str) -> bool:
    """DB value first; else the ``Settings`` field of the same name; ``platform_readonly`` has
    no setting ⇒ ``False``."""
    if key in flags:
        return flags[key]
    return bool(getattr(settings, key, False))


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


__all__ = [
    "CACHE_SECONDS",
    "FLAG_KEYS",
    "READONLY_RETRY_AFTER",
    "FlagCache",
    "FlagRepository",
    "PgFlagRepository",
    "PlatformFlag",
    "PlatformFlagRow",
    "effective",
    "utcnow",
]
