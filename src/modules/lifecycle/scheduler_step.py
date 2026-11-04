"""SPRINT-13 C-3: the ``retention_sweep`` scheduler step — one independent session (D-072)
every ``RETENTION_INTERVAL_SECONDS``; ``RETENTION_DRY_RUN=true`` (default) only counts."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.config import Settings
from src.core.database import get_session_factory, independent_session
from src.modules.lifecycle.domain import RetentionReport
from src.modules.lifecycle.infrastructure.postgres_repository import PgLifecycleRepository
from src.modules.lifecycle.service import LifecycleService
from src.modules.ops.infrastructure.postgres_repository import PgOpsRepository
from src.modules.ops.service import OpsService

logger = logging.getLogger(__name__)


def retention_step(
    settings: Settings,
    *,
    session_factory: Callable[[], async_sessionmaker[AsyncSession]] = get_session_factory,
) -> Callable[[datetime], object]:
    state: dict[str, datetime | None] = {"last_run": None}

    async def step(now: datetime) -> RetentionReport | None:
        last = state["last_run"]
        if last is not None and (now - last).total_seconds() < settings.retention_interval_seconds:
            return None
        state["last_run"] = now
        async with independent_session(session_factory()) as session:
            service = LifecycleService(
                PgLifecycleRepository(session), audit=OpsService(PgOpsRepository(session)).record
            )
            report = await service.retention_sweep(
                now=now,
                months=settings.retention_months,
                months_agency=settings.retention_months_agency,
                export_hours=settings.retention_export_hours,
                dry_run=settings.retention_dry_run,
            )
        logger.info("retention_sweep: dry_run=%s counts=%s", report.dry_run, report.counts)
        return report

    return step


__all__ = ["retention_step"]
