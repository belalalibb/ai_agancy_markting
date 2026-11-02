"""FastAPI wiring for entitlements (SPRINT-11 C-1/C-5).

Only ``src/main.py`` (router mount) and ``src/modules/ai_gateway/dependencies.py``
(``get_entitlement_limits_reader``) import this module — see ``REMOVABILITY.md``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.core.database import get_db_session
from src.modules.ai_gateway.budget import BudgetUsageReader
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
