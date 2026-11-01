"""Domain models for entitlements (SPRINT-11 C-1, D-073). Framework-free, Pydantic only."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, get_args
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

Counter = Literal["monthly_posts", "monthly_wa_conversations"]
EventKind = Literal[
    "activated", "extended", "downgraded", "suspended", "referral_credit", "limits_overridden"
]
EVENT_KINDS: tuple[str, ...] = get_args(EventKind)
REFERRAL_CREDIT_PERCENT = 10  # D-078: audited event only — never an automatic credit
MONTH_DAYS = 30
MAX_ACTIVATION_MONTHS = 12


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


class Plan(StrEnum):
    TRIAL = "trial"
    STARTER = "starter"
    GROWTH = "growth"
    AGENCY = "agency"


class PlanLimits(BaseModel):
    """Per-plan ceilings; every field may be overridden per brand (``limits_overridden``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    daily_tokens: int = Field(..., ge=0)
    monthly_posts: int = Field(..., ge=0)
    monthly_wa_conversations: int = Field(..., ge=0)
    max_brands: int = Field(..., ge=1)
    seats: int = Field(..., ge=1)


DEFAULT_LIMITS: dict[Plan, PlanLimits] = {
    Plan.TRIAL: PlanLimits(
        daily_tokens=20_000, monthly_posts=10, monthly_wa_conversations=50, max_brands=1, seats=1
    ),
    Plan.STARTER: PlanLimits(
        daily_tokens=60_000, monthly_posts=40, monthly_wa_conversations=300, max_brands=1, seats=1
    ),
    Plan.GROWTH: PlanLimits(
        daily_tokens=200_000,
        monthly_posts=150,
        monthly_wa_conversations=1_000,
        max_brands=3,
        seats=3,
    ),
    Plan.AGENCY: PlanLimits(
        daily_tokens=600_000,
        monthly_posts=600,
        monthly_wa_conversations=3_000,
        max_brands=10,
        seats=10,
    ),
}


class BrandEntitlement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    brand_id: UUID
    plan: Plan
    limits: PlanLimits
    active_until: datetime | None = None
    is_active: bool = False
    referred_by_brand_id: UUID | None = None
    updated_at: datetime = Field(default_factory=utcnow)

    def is_active_at(self, now: datetime) -> bool:
        if not self.is_active:
            return False
        return self.active_until is None or self.active_until >= now

    @classmethod
    def trial(
        cls, brand_id: UUID, *, referred_by_brand_id: UUID | None = None
    ) -> BrandEntitlement:
        """A brand without a row behaves as an **active** ``trial`` (C-1)."""
        return cls(
            brand_id=brand_id,
            plan=Plan.TRIAL,
            limits=DEFAULT_LIMITS[Plan.TRIAL],
            is_active=True,
            referred_by_brand_id=referred_by_brand_id,
        )


class EntitlementEvent(BaseModel):
    """Audit row — every admin mutation writes one with its ``actor_user_id`` (C-3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    brand_id: UUID | None
    kind: EventKind
    actor_user_id: UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
