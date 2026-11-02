"""PostgreSQL :class:`BudgetUsageReader` — reads **``ai_cost_log`` only** (SPRINT-11 B-1).

Since every AI call of every feature is reserved and settled in ``ai_cost_log`` by
:class:`~src.modules.ai_gateway.budget_guard.BudgetGuard`, the former union over
``content_revisions`` / ``wa_reply_drafts`` provenance double-counted the same tokens.
Counted outcomes are ``pending`` / ``ok`` / ``ambiguous``; ``error`` rows are free.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.ai_gateway.cost_log import COUNTED_OUTCOMES
from src.modules.ai_gateway.infrastructure.pg_cost_log import AICostLogRow


def _counted_rows(brand_id: UUID, since: datetime):  # noqa: ANN202 - SQLAlchemy clause list
    return (
        AICostLogRow.brand_id == brand_id,
        AICostLogRow.created_at >= since,
        AICostLogRow.outcome.in_(COUNTED_OUTCOMES),
    )


class PgBudgetUsageReader:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def combined_usage_since(self, brand_id: UUID, since: datetime) -> int:
        tokens = func.coalesce(AICostLogRow.prompt_tokens, 0) + func.coalesce(
            AICostLogRow.completion_tokens, 0
        )
        value = await self._session.scalar(
            select(func.coalesce(func.sum(tokens), 0)).where(*_counted_rows(brand_id, since))
        )
        return int(value or 0)

    async def oldest_generation_since(self, brand_id: UUID, since: datetime) -> datetime | None:
        return await self._session.scalar(
            select(func.min(AICostLogRow.created_at)).where(*_counted_rows(brand_id, since))
        )
