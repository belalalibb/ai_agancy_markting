"""``ai_cost_log`` domain contract — owned by ``ai_gateway`` (SPRINT-11 B-1, D-075).

Moved here from ``brand_brain.domain`` so the shared gateway no longer imports a business
module (D-016). The table is the **single** source of AI token usage and platform USD spend:
every gateway call first *reserves* a ``pending`` row (reserve-before-call), then *settles*
it to ``ok`` / ``error`` or *refunds* it when the provider was never reached. Rows that are
never settled (process died mid-call) are turned into ``ambiguous`` by the scheduler and
count against the platform cap conservatively.

Rows carry metadata only — never prompt, output or key text (D-069). ``brand_id`` is
nullable because the financial log outlives the brand (D-081: FK ``ON DELETE SET NULL``).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class CostOutcome(StrEnum):
    PENDING = "pending"
    OK = "ok"
    ERROR = "error"
    AMBIGUOUS = "ambiguous"


#: Outcomes that count towards budgets (``error`` is excluded — the provider did the work
#: for free or not at all; ``pending``/``ambiguous`` are counted conservatively).
COUNTED_OUTCOMES: tuple[str, ...] = (
    CostOutcome.PENDING.value,
    CostOutcome.OK.value,
    CostOutcome.AMBIGUOUS.value,
)


class CostEntry(BaseModel):
    """``ai_cost_log`` row — metadata only, never prompt or output text."""

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    brand_id: UUID | None
    purpose: str = Field(max_length=32)
    provider: str = Field(max_length=32)
    model: str = Field(max_length=64)
    credential_mode: str = Field(max_length=16)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_usd: Decimal | None = Field(default=None, ge=Decimal("0"))
    latency_ms: int = Field(ge=0)
    prompt_version: str = Field(max_length=32)
    outcome: CostOutcome
    error_category: str | None = Field(default=None, max_length=32)
    created_at: datetime
    settled_at: datetime | None = None
    request_id: str | None = Field(default=None, max_length=64)


class CostLogWriter(Protocol):
    """D-072: every method runs on its own committed transaction (``independent_session``)."""

    async def reserve(self, entry: CostEntry) -> UUID:
        """Insert a ``pending`` row **before** the provider call; returns its id."""
        ...

    async def settle(
        self,
        entry_id: UUID,
        *,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        estimated_cost_usd: Decimal | None,
        latency_ms: int,
        outcome: CostOutcome,
        error_category: str | None,
    ) -> None:
        """Finalise a reserved row as ``ok`` or ``error`` (sets ``settled_at``)."""
        ...

    async def refund(self, entry_id: UUID) -> None:
        """Delete a reserved row whose call never reached the provider."""
        ...

    async def platform_usd_since(self, since: datetime) -> Decimal:
        """``SUM(estimated_cost_usd)`` where ``credential_mode='platform'`` and the outcome
        is counted (``pending``/``ok``/``ambiguous``)."""
        ...

    async def brand_tokens_since(self, brand_id: UUID, since: datetime) -> int:
        """``SUM(prompt_tokens + completion_tokens)`` of counted rows of the brand."""
        ...

    async def settle_stale(self, older_than: datetime) -> int:
        """``pending`` rows created before ``older_than`` ⇒ ``ambiguous``; returns count."""
        ...


__all__ = ["COUNTED_OUTCOMES", "CostEntry", "CostLogWriter", "CostOutcome"]
