"""PostgreSQL implementation of the entitlements repositories.

Never commits — the request-scoped session owns the transaction, so ``lock_brand`` +
``upsert`` + ``add_event`` (+ the referral event) are one atomic unit (C-4).

Cross-module reads (``brands``, ``approval_events``, ``wa_conversations``) use SQL text on
purpose: this module must not import another module's ORM (D-016 / D-076).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.entitlements.domain.models import (
    BrandEntitlement,
    Counter,
    EntitlementEvent,
    Plan,
    PlanLimits,
)
from src.modules.entitlements.domain.repository import BrandRef
from src.modules.entitlements.infrastructure.orm import BrandEntitlementRow, EntitlementEventRow

_BRAND_REF = "SELECT id, referred_by_brand_id FROM brands WHERE id = :brand_id"
_COUNTER_SQL: dict[str, str] = {
    "monthly_posts": (
        "SELECT count(*) FROM approval_events e JOIN content_items i ON i.id = e.item_id "
        "WHERE i.brand_id = :brand_id AND e.action = 'approved' AND e.created_at >= :since"
    ),
    "monthly_wa_conversations": (
        "SELECT count(*) FROM wa_conversations WHERE brand_id = :brand_id AND created_at >= :since"
    ),
}


class PgEntitlementRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ----------------------------------------------------------------- brands --
    async def lock_brand(self, brand_id: UUID) -> BrandRef | None:
        row = (
            await self._session.execute(text(_BRAND_REF + " FOR UPDATE"), {"brand_id": brand_id})
        ).first()
        return BrandRef(id=row[0], referred_by_brand_id=row[1]) if row else None

    async def brand_ref(self, brand_id: UUID) -> BrandRef | None:
        row = (await self._session.execute(text(_BRAND_REF), {"brand_id": brand_id})).first()
        return BrandRef(id=row[0], referred_by_brand_id=row[1]) if row else None

    # ----------------------------------------------------------- entitlements --
    async def get(self, brand_id: UUID) -> BrandEntitlement | None:
        row = await self._session.get(BrandEntitlementRow, brand_id)
        if row is None:
            return None
        ref = await self.brand_ref(brand_id)
        return _entitlement(row, ref.referred_by_brand_id if ref else None)

    async def upsert(self, entitlement: BrandEntitlement) -> BrandEntitlement:
        values = {
            "brand_id": entitlement.brand_id,
            "plan": entitlement.plan.value,
            "limits": entitlement.limits.model_dump(),
            "active_until": entitlement.active_until,
            "is_active": entitlement.is_active,
            "updated_at": entitlement.updated_at,
        }
        stmt = pg_insert(BrandEntitlementRow).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[BrandEntitlementRow.brand_id],
            set_={k: v for k, v in values.items() if k != "brand_id"},
        )
        await self._session.execute(stmt)
        return entitlement

    # ----------------------------------------------------------------- events --
    async def add_event(self, event: EntitlementEvent) -> EntitlementEvent:
        self._session.add(
            EntitlementEventRow(
                id=event.id,
                brand_id=event.brand_id,
                kind=event.kind,
                actor_user_id=event.actor_user_id,
                payload=event.payload,
                created_at=event.created_at,
            )
        )
        await self._session.flush()
        return event

    async def has_event(self, brand_id: UUID, kind: str) -> bool:
        row = await self._session.scalar(
            select(EntitlementEventRow.id)
            .where(EntitlementEventRow.brand_id == brand_id, EntitlementEventRow.kind == kind)
            .limit(1)
        )
        return row is not None

    async def list_events(self, brand_id: UUID, *, limit: int) -> list[EntitlementEvent]:
        rows = (
            await self._session.execute(
                select(EntitlementEventRow)
                .where(EntitlementEventRow.brand_id == brand_id)
                .order_by(EntitlementEventRow.created_at.desc(), EntitlementEventRow.id.desc())
                .limit(limit)
            )
        ).scalars()
        return [_event(r) for r in rows]


class PgCounterUsageReader:
    """``CounterUsageReader`` over the existing tables via SQL text (no cross-module ORM)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def monthly_used(self, brand_id: UUID, counter: Counter, *, since: datetime) -> int:
        value = await self._session.scalar(
            text(_COUNTER_SQL[counter]), {"brand_id": brand_id, "since": since}
        )
        return int(value or 0)


# ------------------------------------------------------------------ mappers --
def _entitlement(row: BrandEntitlementRow, referred_by: UUID | None) -> BrandEntitlement:
    return BrandEntitlement(
        brand_id=row.brand_id,
        plan=Plan(row.plan),
        limits=PlanLimits.model_validate(row.limits),
        active_until=row.active_until,
        is_active=row.is_active,
        referred_by_brand_id=referred_by,
        updated_at=row.updated_at,
    )


def _event(row: EntitlementEventRow) -> EntitlementEvent:
    return EntitlementEvent(
        id=row.id,
        brand_id=row.brand_id,
        kind=row.kind,  # type: ignore[arg-type]
        actor_user_id=row.actor_user_id,
        payload=dict(row.payload or {}),
        created_at=row.created_at,
    )
