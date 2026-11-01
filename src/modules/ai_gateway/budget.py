"""The **single** daily AI token budget reader (SPRINT-10 A-4, D-073 groundwork).

Every AI feature — content generation (SPRINT-07 A-5), WhatsApp reply suggestions
(SPRINT-09 B-3) and Brand Brain (M7) — asks :class:`DailyBudget.check` the same question
through one :class:`BudgetUsageReader`; the PostgreSQL implementation lives in
:mod:`src.modules.ai_gateway.infrastructure.pg_budget` and is the only place in ``src/``
that aggregates token usage. SPRINT-11 swaps the limit source to ``brand_entitlements``
without touching the callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

TOKEN_BUDGET_WINDOW = timedelta(hours=24)


class BudgetUsageReader(Protocol):
    async def combined_usage_since(self, brand_id: UUID, since: datetime) -> int:
        """``prompt_tokens + completion_tokens`` of every AI call of the brand since
        ``since`` across all features (one query)."""
        ...

    async def oldest_generation_since(self, brand_id: UUID, since: datetime) -> datetime | None:
        """``created_at`` of the oldest counted call in the window — drives ``Retry-After``."""
        ...


class NullBudgetUsageReader:
    """No usage at all (unit tests / services constructed without persistence)."""

    async def combined_usage_since(self, brand_id: UUID, since: datetime) -> int:
        return 0

    async def oldest_generation_since(self, brand_id: UUID, since: datetime) -> datetime | None:
        return None


@dataclass(frozen=True, slots=True)
class BudgetVerdict:
    allowed: bool
    budget: int
    used: int
    resets_in_seconds: int  # meaningful only when ``allowed`` is False


class DailyBudget:
    """``used >= budget`` over the rolling 24h window ⇒ not allowed (callers map to 429)."""

    def __init__(self, daily_token_budget: int) -> None:
        self._limit = daily_token_budget

    @property
    def limit(self) -> int:
        return self._limit

    async def check(
        self,
        brand_id: UUID,
        *,
        usage_reader: BudgetUsageReader,
        now: datetime | None = None,
    ) -> BudgetVerdict:
        now = now or datetime.now(tz=UTC)
        since = now - TOKEN_BUDGET_WINDOW
        used = await usage_reader.combined_usage_since(brand_id, since)
        if used < self._limit:
            return BudgetVerdict(allowed=True, budget=self._limit, used=used, resets_in_seconds=0)
        oldest = await usage_reader.oldest_generation_since(brand_id, since)
        resets_in = (
            max(int((oldest + TOKEN_BUDGET_WINDOW - now).total_seconds()), 1)
            if oldest is not None
            else int(TOKEN_BUDGET_WINDOW.total_seconds())
        )
        return BudgetVerdict(
            allowed=False, budget=self._limit, used=used, resets_in_seconds=resets_in
        )
