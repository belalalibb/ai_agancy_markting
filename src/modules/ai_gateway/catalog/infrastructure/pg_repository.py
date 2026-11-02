"""PostgreSQL repository for the provider catalog (SPRINT-11.1 B-2, D-077).

Never commits — the request-scoped session owns the transaction. Unique-constraint
violations surface as ``CatalogConflictError`` (409) so the admin router never leaks SQL.

``CatalogCache`` is the **in-process** snapshot cache (``AI_CATALOG_CACHE_SECONDS``,
default 30): the hot path (``CatalogGatewayResolver``) reads one snapshot per TTL window
instead of three queries per completion; every admin write calls ``invalidate()``.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.database import independent_session
from src.modules.ai_gateway.catalog.domain import (
    CatalogSnapshot,
    Model,
    ModelPricing,
    Provider,
    Route,
)
from src.modules.ai_gateway.catalog.infrastructure.orm import (
    AIModelRow,
    AIProviderRow,
    AIRoutingRow,
)
from src.modules.ai_gateway.catalog.service import CatalogConflictError, CatalogNotFoundError

# ------------------------------------------------------------------ mappers --


def _provider(row: AIProviderRow) -> Provider:
    return Provider(
        id=row.id,
        slug=row.slug,
        kind=row.kind,  # type: ignore[arg-type]
        base_url=row.base_url,
        credential_cipher=row.credential_cipher,
        credential_ref=row.credential_ref,
        enabled=row.enabled,
        timeout_seconds=Decimal(row.timeout_seconds),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _model(row: AIModelRow) -> Model:
    return Model(
        id=row.id,
        provider_id=row.provider_id,
        model_name=row.model_name,
        capability=row.capability,  # type: ignore[arg-type]
        pricing=ModelPricing(
            prompt_per_1k=row.prompt_per_1k,
            completion_per_1k=row.completion_per_1k,
            cost_per_minute_usd=row.cost_per_minute_usd,
            max_audio_seconds=row.max_audio_seconds,
        ),
        enabled=row.enabled,
        max_tokens_cap=row.max_tokens_cap,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _route(row: AIRoutingRow) -> Route:
    return Route(
        id=row.id,
        purpose=row.purpose,  # type: ignore[arg-type]
        model_id=row.model_id,
        priority=row.priority,
        enabled=row.enabled,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _provider_values(p: Provider) -> dict[str, object]:
    return {
        "slug": p.slug,
        "kind": p.kind,
        "base_url": p.base_url,
        "credential_cipher": p.credential_cipher,
        "credential_ref": p.credential_ref,
        "enabled": p.enabled,
        "timeout_seconds": p.timeout_seconds,
        "updated_at": p.updated_at,
    }


def _model_values(m: Model) -> dict[str, object]:
    return {
        "provider_id": m.provider_id,
        "model_name": m.model_name,
        "capability": m.capability,
        "prompt_per_1k": m.pricing.prompt_per_1k,
        "completion_per_1k": m.pricing.completion_per_1k,
        "cost_per_minute_usd": m.pricing.cost_per_minute_usd,
        "max_audio_seconds": m.pricing.max_audio_seconds,
        "enabled": m.enabled,
        "max_tokens_cap": m.max_tokens_cap,
        "updated_at": m.updated_at,
    }


# --------------------------------------------------------------- repository --


class PgCatalogRepository:
    """CRUD + snapshot over ``ai_providers`` / ``ai_models`` / ``ai_routing``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _flush(self) -> None:
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise CatalogConflictError("catalog row conflicts with an existing one") from exc

    # providers ---------------------------------------------------------------
    async def list_providers(self) -> list[Provider]:
        rows = await self._session.scalars(select(AIProviderRow).order_by(AIProviderRow.slug))
        return [_provider(r) for r in rows]

    async def get_provider(self, provider_id: UUID) -> Provider | None:
        row = await self._session.get(AIProviderRow, provider_id)
        return _provider(row) if row else None

    async def add_provider(self, provider: Provider) -> Provider:
        self._session.add(
            AIProviderRow(
                id=provider.id, created_at=provider.created_at, **_provider_values(provider)
            )
        )
        await self._flush()
        return provider

    async def update_provider(self, provider: Provider) -> Provider:
        row = await self._session.get(AIProviderRow, provider.id)
        if row is None:
            raise CatalogNotFoundError("provider not found")
        for key, value in _provider_values(provider).items():
            setattr(row, key, value)
        await self._flush()
        return provider

    async def delete_provider(self, provider_id: UUID) -> bool:
        result = await self._session.execute(
            delete(AIProviderRow).where(AIProviderRow.id == provider_id)
        )
        return bool(result.rowcount)

    # models ------------------------------------------------------------------
    async def list_models(self, provider_id: UUID | None = None) -> list[Model]:
        stmt = select(AIModelRow).order_by(AIModelRow.model_name)
        if provider_id is not None:
            stmt = stmt.where(AIModelRow.provider_id == provider_id)
        return [_model(r) for r in await self._session.scalars(stmt)]

    async def get_model(self, model_id: UUID) -> Model | None:
        row = await self._session.get(AIModelRow, model_id)
        return _model(row) if row else None

    async def add_model(self, model: Model) -> Model:
        self._session.add(
            AIModelRow(id=model.id, created_at=model.created_at, **_model_values(model))
        )
        await self._flush()
        return model

    async def update_model(self, model: Model) -> Model:
        row = await self._session.get(AIModelRow, model.id)
        if row is None:
            raise CatalogNotFoundError("model not found")
        for key, value in _model_values(model).items():
            setattr(row, key, value)
        await self._flush()
        return model

    async def delete_model(self, model_id: UUID) -> bool:
        result = await self._session.execute(delete(AIModelRow).where(AIModelRow.id == model_id))
        return bool(result.rowcount)

    # routing -----------------------------------------------------------------
    async def list_routes(self, purpose: str | None = None) -> list[Route]:
        stmt = select(AIRoutingRow).order_by(AIRoutingRow.purpose, AIRoutingRow.priority)
        if purpose is not None:
            stmt = stmt.where(AIRoutingRow.purpose == purpose)
        return [_route(r) for r in await self._session.scalars(stmt)]

    async def replace_routes(self, purpose: str, routes: list[Route]) -> list[Route]:
        """Atomic replace of one purpose's chain (PUT /admin/ai/routing/{purpose})."""
        await self._session.execute(delete(AIRoutingRow).where(AIRoutingRow.purpose == purpose))
        for route in routes:
            self._session.add(
                AIRoutingRow(
                    id=route.id,
                    purpose=purpose,
                    model_id=route.model_id,
                    priority=route.priority,
                    enabled=route.enabled,
                    created_at=route.created_at,
                    updated_at=route.updated_at,
                )
            )
        await self._flush()
        return routes

    # snapshot ----------------------------------------------------------------
    async def snapshot(self) -> CatalogSnapshot:
        return CatalogSnapshot(
            providers=tuple(await self.list_providers()),
            models=tuple(await self.list_models()),
            routes=tuple(await self.list_routes()),
        )


# -------------------------------------------------------------------- cache --


class CatalogCache:
    """Process-wide snapshot cache with a monotonic TTL; ``ttl_seconds <= 0`` disables it."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._snapshot: CatalogSnapshot | None = None
        self._expires_at = 0.0

    def get(self, ttl_seconds: float) -> CatalogSnapshot | None:
        if self._snapshot is None or ttl_seconds <= 0 or self._clock() >= self._expires_at:
            return None
        return self._snapshot

    def put(self, snapshot: CatalogSnapshot, ttl_seconds: float) -> None:
        self._snapshot = snapshot
        self._expires_at = self._clock() + ttl_seconds

    def invalidate(self) -> None:
        self._snapshot = None
        self._expires_at = 0.0


#: The single in-process cache — invalidated by every ``/admin/ai/*`` write.
CATALOG_CACHE = CatalogCache()


SnapshotLoader = Callable[[], Awaitable[CatalogSnapshot]]


async def load_snapshot_independently(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> CatalogSnapshot:
    """One short independent read transaction (D-072) — the hot path never borrows the
    request session, so the switch-off path stays free of any catalog I/O."""
    async with independent_session(session_factory) as session:
        return await PgCatalogRepository(session).snapshot()


class CachedCatalogReader:
    """``CatalogReader`` that serves ``CATALOG_CACHE`` and refills it through ``load``."""

    def __init__(
        self,
        load: SnapshotLoader = load_snapshot_independently,
        *,
        ttl_seconds: float,
        cache: CatalogCache = CATALOG_CACHE,
    ) -> None:
        self._load = load
        self._ttl = ttl_seconds
        self._cache = cache

    async def snapshot(self) -> CatalogSnapshot:
        cached = self._cache.get(self._ttl)
        if cached is not None:
            return cached
        fresh = await self._load()
        self._cache.put(fresh, self._ttl)
        return fresh
