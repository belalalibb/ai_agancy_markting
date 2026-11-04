"""Pulse scheduler step (SPRINT-12 B-3): ``Pulse.run(now)`` every ``PULSE_INTERVAL_SECONDS``.

Plugged into the publish scheduler as a second ``extra_steps`` entry by ``src/main.py``;
the loop calls it every poll, the step itself skips until the interval has elapsed. Each
run uses one independent session (D-072) so a failing check never touches a request.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.database import get_session_factory, independent_session
from src.modules.pulse.domain import PlanCounterUsageReader
from src.modules.pulse.infrastructure.postgres_repository import PgPulseRepository
from src.modules.pulse.service import Pulse

logger = logging.getLogger(__name__)

CountersFactory = Callable[[AsyncSession], PlanCounterUsageReader]
FlagReader = Callable[[AsyncSession], Awaitable[bool]]  # F-6: injected by ``src/main.py``


def pulse_step(
    *,
    interval_seconds: int,
    counters_factory: CountersFactory | None = None,
    flag_reader: FlagReader | None = None,
    session_factory: Callable[[], async_sessionmaker[AsyncSession]] = get_session_factory,
) -> Callable[[datetime], object]:
    """Build the step; ``last_run`` is kept in the closure. SPRINT-13.1 F-6: ``flag_reader``
    (D-016 port, bound to ``feature_pulse`` in ``main.py``) is asked every cycle inside the
    session — ``False`` ⇒ ``0`` and no ``pulse_alerts`` row."""
    state: dict[str, datetime | None] = {"last_run": None}

    async def step(now: datetime) -> int:
        last = state["last_run"]
        if last is not None and (now - last).total_seconds() < interval_seconds:
            return 0
        state["last_run"] = now
        async with independent_session(session_factory()) as session:
            if flag_reader is not None and not await flag_reader(session):
                logger.info("pulse: feature_pulse off (platform_flags) — cycle skipped")
                return 0
            reader = counters_factory(session) if counters_factory is not None else None
            created = await Pulse(PgPulseRepository(session), counters_reader=reader).run(now)
        return len(created)

    return step


__all__ = ["CountersFactory", "FlagReader", "pulse_step"]
