"""Storage contracts for entitlements (Protocols only — D-013 / D-076)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from src.modules.entitlements.domain.models import BrandEntitlement, Counter, EntitlementEvent


@dataclass(frozen=True, slots=True)
class BrandRef:
    """What entitlements needs to know about a brand — read through SQL, not tenancy ORM."""

    id: UUID
    referred_by_brand_id: UUID | None


class EntitlementRepository(Protocol):
    async def lock_brand(self, brand_id: UUID) -> BrandRef | None:
        """``SELECT … FOR UPDATE`` on the brand row (serialises concurrent activations —
        C-4); ``None`` when the brand does not exist."""
        ...

    async def brand_ref(self, brand_id: UUID) -> BrandRef | None: ...

    async def get(self, brand_id: UUID) -> BrandEntitlement | None: ...

    async def upsert(self, entitlement: BrandEntitlement) -> BrandEntitlement: ...

    async def add_event(self, event: EntitlementEvent) -> EntitlementEvent: ...

    async def has_event(self, brand_id: UUID, kind: str) -> bool: ...

    async def list_events(self, brand_id: UUID, *, limit: int) -> list[EntitlementEvent]:
        """Newest first."""
        ...


class CounterUsageReader(Protocol):
    """Monthly consumption of the existing tables (``approval_events`` / ``wa_conversations``)
    — read by SQL text, never by importing another module's ORM (D-016)."""

    async def monthly_used(self, brand_id: UUID, counter: Counter, *, since: datetime) -> int: ...
