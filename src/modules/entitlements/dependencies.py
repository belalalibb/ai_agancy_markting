"""FastAPI wiring for entitlements (SPRINT-11 C-1/C-5).

Only ``src/main.py`` (router mount) and ``src/modules/ai_gateway/dependencies.py``
(``get_entitlement_limits_reader``) import this module — see ``REMOVABILITY.md``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends
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
from src.modules.entitlements.service import EntitlementService, PgEntitlementLimitsReader


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

    It raises the **consumer's** ``CounterQuotaExceededError`` class (passed in by ``src/main.py``
    at bind time) so neither consumer ever imports this module (D-016 / D-076).
    """

    def __init__(self, limits: PgEntitlementLimitsReader, *, exceeded: type[Exception]) -> None:
        self._limits = limits
        self._exceeded = exceeded

    async def _ensure(self, brand_id: UUID, counter: str, *, reserve: int) -> None:
        try:
            await self._limits.check_counter(brand_id, counter, reserve=reserve)  # type: ignore[arg-type]
        except EntitlementLimitError as exc:
            if exc.limit_kind != counter:  # plan_inactive keeps its 429 contract
                raise
            raise self._exceeded(counter, limit=exc.budget or 0, used=exc.used or 0) from exc

    async def ensure_can_approve_post(self, brand_id: UUID) -> None:
        """One more approval allowed? (``used + 1 > monthly_posts`` ⇒ refused)."""
        await self._ensure(brand_id, "monthly_posts", reserve=1)

    async def ensure_within_conversation_quota(self, brand_id: UUID) -> None:
        """Already over? (``used > monthly_wa_conversations`` ⇒ refused) — the conversation
        under review is already stored, so the 50th of 50 passes and the 51st is refused."""
        await self._ensure(brand_id, "monthly_wa_conversations", reserve=0)


def counter_guard_dependency(exceeded: type[Exception]) -> Callable[..., PlanCounterGuard]:
    """Build the FastAPI dependency ``src/main.py`` installs as the override of a consumer's
    ``get_counter_guard`` (one per consumer, each with its own exception class)."""

    def _get(limits: Limits) -> PlanCounterGuard:
        return PlanCounterGuard(limits, exceeded=exceeded)

    return _get
