"""FastAPI wiring for entitlements (SPRINT-11 C-1/C-5).

Only ``src/main.py`` (router mount) and ``src/modules/ai_gateway/dependencies.py``
(``get_entitlement_limits_reader``) import this module — see ``REMOVABILITY.md``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.core.database import get_db_session
from src.modules.ai_gateway.budget import BudgetUsageReader
from src.modules.ai_gateway.budget_guard import EntitlementLimitError
from src.modules.ai_gateway.dependencies import get_budget_usage_reader
from src.modules.entitlements.domain.repository import CounterUsageReader, EntitlementRepository
from src.modules.entitlements.infrastructure.postgres_repository import (
    PgCounterUsageReader,
    PgEntitlementRepository,
)
from src.modules.entitlements.service import (
    EntitlementService,
    PgEntitlementLimitsReader,
    PlanCounterUsageRow,
    plan_counter_usage,
)


def get_entitlement_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> EntitlementRepository:
    return PgEntitlementRepository(session)


def get_counter_usage_reader(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CounterUsageReader:
    return PgCounterUsageReader(session)


Repo = Annotated[EntitlementRepository, Depends(get_entitlement_repository)]
Counters = Annotated[CounterUsageReader, Depends(get_counter_usage_reader)]
UsageReader = Annotated[BudgetUsageReader, Depends(get_budget_usage_reader)]


def get_entitlement_service(repo: Repo) -> EntitlementService:
    return EntitlementService(repo)


def get_limits_reader(
    repo: Repo,
    usage_reader: UsageReader,
    counters: Counters,
    settings: Annotated[Settings, Depends(get_settings)],
) -> PgEntitlementLimitsReader:
    """The ``EntitlementLimitsReader`` injected into ``BudgetGuard`` (kill switch honoured)."""
    return PgEntitlementLimitsReader(
        repo,
        usage_reader=usage_reader,
        counters=counters,
        enforced=settings.feature_entitlements_enforced,
    )


Service = Annotated[EntitlementService, Depends(get_entitlement_service)]
Limits = Annotated[PgEntitlementLimitsReader, Depends(get_limits_reader)]


# ------------------------------------------------- SPRINT-11.1 A-2 (E2) counter guards --
class PlanCounterGuard:
    """Concrete adapter for the consumer-owned ``CounterGuard`` protocols of ``content``
    (``ensure_can_approve_post``) and ``whatsapp`` (``ensure_within_conversation_quota``).

    It raises the **consumer's** ``CounterQuotaExceededError`` / ``CounterPlanInactiveError``
    classes (passed in by ``src/main.py`` at bind time) so neither consumer ever imports this
    module (D-016 / D-076). SPRINT-12 A-1 (H1): ``plan_inactive`` is translated too — the
    consumer maps it to **402 ``entitlement_plan_inactive``** (the webhook to a 200 flag).
    """

    def __init__(
        self,
        limits: PgEntitlementLimitsReader,
        *,
        exceeded: type[Exception],
        inactive: type[Exception] | None = None,
    ) -> None:
        self._limits = limits
        self._exceeded = exceeded
        self._inactive = inactive

    async def _ensure(self, brand_id: UUID, counter: str, *, reserve: int) -> None:
        try:
            await self._limits.check_counter(brand_id, counter, reserve=reserve)  # type: ignore[arg-type]
        except EntitlementLimitError as exc:
            if exc.limit_kind == "plan_inactive" and self._inactive is not None:
                raise self._inactive(plan=exc.plan, active_until=exc.active_until) from exc
            if exc.limit_kind != counter:  # plan_inactive without a consumer class ⇒ raw
                raise
            raise self._exceeded(counter, limit=exc.budget or 0, used=exc.used or 0) from exc

    async def ensure_can_approve_post(self, brand_id: UUID) -> None:
        """One more approval allowed? (``used + 1 > monthly_posts`` ⇒ refused)."""
        await self._ensure(brand_id, "monthly_posts", reserve=1)

    async def ensure_within_conversation_quota(self, brand_id: UUID) -> None:
        """Already over? (``used > monthly_wa_conversations`` ⇒ refused) — the conversation
        under review is already stored, so the 50th of 50 passes and the 51st is refused."""
        await self._ensure(brand_id, "monthly_wa_conversations", reserve=0)


def counter_guard_dependency(
    exceeded: type[Exception], inactive: type[Exception] | None = None
) -> Callable[..., PlanCounterGuard]:
    """Build the FastAPI dependency ``src/main.py`` installs as the override of a consumer's
    ``get_counter_guard`` (one per consumer, each with its own exception classes)."""

    def _get(limits: Limits) -> PlanCounterGuard:
        return PlanCounterGuard(limits, exceeded=exceeded, inactive=inactive)

    return _get


# ------------------------------------------------------- SPRINT-12 B-3 (Pulse check 6) --
_ACTIVE_BRANDS_SQL = """
SELECT brand_id FROM wa_conversations WHERE created_at >= :since
UNION
SELECT brand_id FROM publish_jobs WHERE created_at >= :since
"""


class PgPlanCounterUsageReader:
    """``pulse.domain.PlanCounterUsageReader`` adapter bound in ``src/main.py`` (D-016):
    the brands that consumed any counter this month, with ``used / limit`` per counter."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def usage(self, now: datetime) -> list[PlanCounterUsageRow]:
        from src.modules.entitlements.service import MONTH_WINDOW

        result = await self._session.execute(
            text(_ACTIVE_BRANDS_SQL), {"since": now - MONTH_WINDOW}
        )
        brand_ids: list[UUID] = [row[0] for row in result.all()]
        return await plan_counter_usage(
            brand_ids,
            repo=PgEntitlementRepository(self._session),
            counters=PgCounterUsageReader(self._session),
            now=now,
        )
