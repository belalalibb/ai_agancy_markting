"""Pydantic v2 API contracts for entitlements (SPRINT-11 C-5)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.modules.entitlements.domain.models import (
    MAX_ACTIVATION_MONTHS,
    BrandEntitlement,
    EntitlementEvent,
    Plan,
    PlanLimits,
)

LearningConsent = Literal["signals_only", "none", "full"]


class ActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    brand_id: UUID
    plan: Plan
    months: int = Field(..., ge=1, le=MAX_ACTIVATION_MONTHS)
    note: str | None = Field(default=None, max_length=256)


class EntitlementEventOut(BaseModel):
    id: UUID
    kind: str
    actor_user_id: UUID | None
    payload: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_domain(cls, event: EntitlementEvent) -> EntitlementEventOut:
        return cls(
            id=event.id,
            kind=event.kind,
            actor_user_id=event.actor_user_id,
            payload=event.payload,
            created_at=event.created_at,
        )


class BrandEntitlementOut(BaseModel):
    """Admin view (C-5 routes 1–2): includes ``referred_by_brand_id`` and recent events."""

    brand_id: UUID
    plan: Plan
    limits: PlanLimits
    active_until: datetime | None
    is_active: bool
    referred_by_brand_id: UUID | None
    updated_at: datetime
    events: list[EntitlementEventOut] = Field(default_factory=list)

    @classmethod
    def from_domain(
        cls, ent: BrandEntitlement, events: list[EntitlementEvent] | None = None
    ) -> BrandEntitlementOut:
        return cls(
            brand_id=ent.brand_id,
            plan=ent.plan,
            limits=ent.limits,
            active_until=ent.active_until,
            is_active=ent.is_active,
            referred_by_brand_id=ent.referred_by_brand_id,
            updated_at=ent.updated_at,
            events=[EntitlementEventOut.from_domain(e) for e in events or []],
        )


class BrandEntitlementPublic(BaseModel):
    """Brand-member view (C-5 route 3): plan, limits and **current usage** — no events, no
    ``referred_by``."""

    plan: Plan
    active_until: datetime | None
    is_active: bool
    limits: PlanLimits
    usage: dict[str, int]

    @classmethod
    def from_domain(cls, ent: BrandEntitlement, usage: dict[str, int]) -> BrandEntitlementPublic:
        return cls(
            plan=ent.plan,
            active_until=ent.active_until,
            is_active=ent.is_active,
            limits=ent.limits,
            usage=usage,
        )
