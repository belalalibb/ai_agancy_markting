"""Entitlement service (SPRINT-11 C-1/C-4) + the ``EntitlementLimitsReader`` for ``BudgetGuard``.

Plan ``daily_tokens`` **replaces** ``ai_daily_token_budget_per_brand`` whenever the brand has
an entitlement row; a brand without a row is an active ``trial`` (C-1). Every admin mutation
writes an ``entitlement_events`` row with its ``actor_user_id`` (C-3). The referral credit
(D-078) is an **audited event only** — no automatic balance, no limit change.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from src.modules.ai_gateway.budget import TOKEN_BUDGET_WINDOW, BudgetUsageReader
from src.modules.ai_gateway.budget_guard import EntitlementLimitError
from src.modules.entitlements.domain.models import (
    DEFAULT_LIMITS,
    MAX_ACTIVATION_MONTHS,
    MONTH_DAYS,
    REFERRAL_CREDIT_PERCENT,
    BrandEntitlement,
    Counter,
    EntitlementEvent,
    Plan,
    PlanLimits,
    utcnow,
)
from src.modules.entitlements.domain.repository import CounterUsageReader, EntitlementRepository
from src.modules.entitlements.exceptions import BrandNotFoundError, InvalidActivationError

logger = logging.getLogger(__name__)

MONTH_WINDOW = timedelta(days=MONTH_DAYS)
EVENTS_LIMIT = 20
Clock = Callable[[], datetime]


class EntitlementService:
    def __init__(self, repo: EntitlementRepository, *, clock: Clock = utcnow) -> None:
        self._repo = repo
        self._clock = clock

    # ------------------------------------------------------------------ reads --
    async def get(self, brand_id: UUID) -> BrandEntitlement:
        """The stored row, or the implicit ``trial`` for a brand without one (404 when the
        brand itself does not exist)."""
        ref = await self._repo.brand_ref(brand_id)
        if ref is None:
            raise BrandNotFoundError()
        stored = await self._repo.get(brand_id)
        return stored or BrandEntitlement.trial(
            brand_id, referred_by_brand_id=ref.referred_by_brand_id
        )

    async def recent_events(self, brand_id: UUID) -> list[EntitlementEvent]:
        return await self._repo.list_events(brand_id, limit=EVENTS_LIMIT)

    # ---------------------------------------------------------------- writes --
    async def activate(
        self,
        brand_id: UUID,
        plan: Plan,
        months: int,
        *,
        actor_user_id: UUID,
        note: str | None = None,
    ) -> BrandEntitlement:
        """Upsert; ``active_until = max(now, active_until) + months × 30 d``; ``is_active``.
        First ``activated`` of a referred brand ⇒ one ``referral_credit`` for the referrer."""
        if not 1 <= months <= MAX_ACTIVATION_MONTHS:
            raise InvalidActivationError(f"months must be within 1..{MAX_ACTIVATION_MONTHS}")
        ref = await self._repo.lock_brand(brand_id)  # FOR UPDATE — serialises activations
        if ref is None:
            raise BrandNotFoundError()
        now = self._clock()
        current = await self._repo.get(brand_id)
        base = now
        if current is not None and current.active_until is not None:
            base = max(now, current.active_until)
        active_until = base + months * MONTH_WINDOW
        limits = current.limits if current is not None and current.plan == plan else None
        updated = BrandEntitlement(
            brand_id=brand_id,
            plan=plan,
            limits=limits or DEFAULT_LIMITS[plan],
            active_until=active_until,
            is_active=True,
            referred_by_brand_id=ref.referred_by_brand_id,
            updated_at=now,
        )
        await self._repo.upsert(updated)
        first_activation = not await self._repo.has_event(brand_id, "activated")
        kind = "activated" if first_activation else "extended"
        payload: dict[str, Any] = {
            "plan": plan.value,
            "months": months,
            "active_until": active_until.isoformat(),
        }
        if note:
            payload["note"] = note[:256]
        await self._repo.add_event(
            EntitlementEvent(
                brand_id=brand_id,
                kind=kind,  # type: ignore[arg-type]
                actor_user_id=actor_user_id,
                payload=payload,
                created_at=now,
            )
        )
        if first_activation and ref.referred_by_brand_id is not None:
            await self._repo.add_event(
                EntitlementEvent(
                    brand_id=ref.referred_by_brand_id,
                    kind="referral_credit",
                    actor_user_id=actor_user_id,
                    payload={
                        "referred_brand_id": str(brand_id),
                        "percent": REFERRAL_CREDIT_PERCENT,
                        "plan": plan.value,
                        "months": months,
                    },
                    created_at=now,
                )
            )
            logger.info(
                "entitlements referral_credit: referrer=%s referred=%s plan=%s months=%d",
                ref.referred_by_brand_id,
                brand_id,
                plan.value,
                months,
            )
        logger.info("entitlements %s: brand=%s plan=%s by=%s", kind, brand_id, plan, actor_user_id)
        return updated

    async def set_limits_override(
        self, brand_id: UUID, limits: PlanLimits, *, actor_user_id: UUID
    ) -> BrandEntitlement:
        ref = await self._repo.lock_brand(brand_id)
        if ref is None:
            raise BrandNotFoundError()
        now = self._clock()
        current = await self.get(brand_id)
        updated = current.model_copy(update={"limits": limits, "updated_at": now})
        await self._repo.upsert(updated)
        await self._repo.add_event(
            EntitlementEvent(
                brand_id=brand_id,
                kind="limits_overridden",
                actor_user_id=actor_user_id,
                payload={"limits": limits.model_dump()},
                created_at=now,
            )
        )
        return updated

    async def suspend(
        self, brand_id: UUID, *, actor_user_id: UUID, reason: str | None = None
    ) -> BrandEntitlement:
        ref = await self._repo.lock_brand(brand_id)
        if ref is None:
            raise BrandNotFoundError()
        now = self._clock()
        current = await self.get(brand_id)
        updated = current.model_copy(update={"is_active": False, "updated_at": now})
        await self._repo.upsert(updated)
        await self._repo.add_event(
            EntitlementEvent(
                brand_id=brand_id,
                kind="suspended",
                actor_user_id=actor_user_id,
                payload={"reason": (reason or "")[:256]},
                created_at=now,
            )
        )
        return updated


class PgEntitlementLimitsReader:
    """``ai_gateway.budget.EntitlementLimitsReader`` (C-1) backed by the entitlement
    repository. ``enforced=False`` (kill switch ``FEATURE_ENTITLEMENTS_ENFORCED``) ⇒ always
    allowed. Raises :class:`EntitlementLimitError` — 429 ``generation_budget_exceeded``
    with ``limit_kind`` = ``plan_inactive`` / ``plan_daily_tokens`` / ``monthly_posts`` /
    ``monthly_wa_conversations``; no new error code (D-067).
    """

    def __init__(
        self,
        repo: EntitlementRepository,
        *,
        usage_reader: BudgetUsageReader,
        counters: CounterUsageReader | None = None,
        enforced: bool = True,
        clock: Clock = utcnow,
    ) -> None:
        self._repo = repo
        self._usage = usage_reader
        self._counters = counters
        self._enforced = enforced
        self._clock = clock

    async def _active_entitlement(
        self, brand_id: UUID, now: datetime
    ) -> tuple[BrandEntitlement, bool]:
        """``(entitlement, stored)`` — ``stored=False`` ⇒ the implicit ``trial`` of a brand
        without a row. Raises ``plan_inactive`` when suspended / expired."""
        stored = await self._repo.get(brand_id)
        entitlement = stored or BrandEntitlement.trial(brand_id)
        if not entitlement.is_active_at(now):
            resets = 1
            if entitlement.active_until is not None and entitlement.active_until > now:
                resets = int((entitlement.active_until - now).total_seconds())  # pragma: no cover
            raise EntitlementLimitError("plan_inactive", resets_in_seconds=max(resets, 3600))
        return entitlement, stored is not None

    async def check(self, brand_id: UUID, purpose: str) -> bool:
        """C-1 / SPRINT-11.1 A-2 (E2c): ``True`` ⇒ the brand's plan governs it and its
        ``daily_tokens`` **replace** ``ai_daily_token_budget_per_brand`` — a brand **without a
        row is governed by the implicit ``trial`` limits** (20,000), so what ``GET /entitlement``
        shows is what is enforced. ``False`` **only** under the kill switch
        (``FEATURE_ENTITLEMENTS_ENFORCED=false``) — then the ``BudgetGuard`` applies the global
        setting as the fallback ceiling."""
        if not self._enforced:
            return False
        now = self._clock()
        entitlement, _stored = await self._active_entitlement(brand_id, now)
        budget = entitlement.limits.daily_tokens
        since = now - TOKEN_BUDGET_WINDOW
        used = await self._usage.combined_usage_since(brand_id, since)
        if used >= budget:
            oldest = await self._usage.oldest_generation_since(brand_id, since)
            resets = (
                int((oldest + TOKEN_BUDGET_WINDOW - now).total_seconds())
                if oldest is not None
                else int(TOKEN_BUDGET_WINDOW.total_seconds())
            )
            logger.info(
                "entitlements plan_daily_tokens: brand=%s purpose=%s used=%d budget=%d",
                brand_id,
                purpose,
                used,
                budget,
            )
            raise EntitlementLimitError(
                "plan_daily_tokens", budget=budget, used=used, resets_in_seconds=resets
            )
        return True

    async def check_counter(self, brand_id: UUID, counter: Counter, *, reserve: int = 1) -> None:
        """Monthly ceilings read through the consumer-defined ``CounterUsageReader``.

        ``reserve`` = how many more units the caller is about to consume: ``1`` (default)
        asks *"may I add one more?"* (``used + 1 > budget`` refuses); ``0`` asks *"is the
        brand already over its ceiling?"* (SPRINT-11.1 A-2 — the WhatsApp webhook evaluates
        the conversation it has **already stored**, so the 50th of 50 is fine and the 51st
        is flagged)."""
        if not self._enforced or self._counters is None:
            return
        now = self._clock()
        entitlement, _stored = await self._active_entitlement(brand_id, now)
        budget = getattr(entitlement.limits, counter)
        used = await self._counters.monthly_used(brand_id, counter, since=now - MONTH_WINDOW)
        if used + reserve > budget:
            raise EntitlementLimitError(
                counter,
                budget=budget,
                used=used,
                resets_in_seconds=int(MONTH_WINDOW.total_seconds()),
            )


async def current_usage(
    entitlement: BrandEntitlement,
    *,
    usage_reader: BudgetUsageReader,
    counters: CounterUsageReader,
    now: datetime,
) -> dict[str, int]:
    """``BrandEntitlementPublic.usage`` (C-5 route 3): the current consumption per limit."""
    return {
        "daily_tokens": await usage_reader.combined_usage_since(
            entitlement.brand_id, now - TOKEN_BUDGET_WINDOW
        ),
        "monthly_posts": await counters.monthly_used(
            entitlement.brand_id, "monthly_posts", since=now - MONTH_WINDOW
        ),
        "monthly_wa_conversations": await counters.monthly_used(
            entitlement.brand_id, "monthly_wa_conversations", since=now - MONTH_WINDOW
        ),
    }
