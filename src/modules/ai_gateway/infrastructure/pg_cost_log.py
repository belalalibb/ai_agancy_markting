"""PostgreSQL ``ai_cost_log`` — ORM row + :class:`CostLogWriter` (SPRINT-11 B-1/B-2).

Moved from ``brand_brain.infrastructure`` (D-016) and extended for reserve-before-call
(migration 0013): ``outcome`` is ``VARCHAR(16)`` with ``pending``/``ambiguous``; ``brand_id``
is nullable with ``ON DELETE SET NULL`` (D-081); ``settled_at`` / ``request_id`` are new;
``ix_ai_cost_log_mode_created`` serves the platform cap and the partial
``ix_ai_cost_log_pending`` serves ``settle_stale``.

Every writer method runs on an ``independent_session()`` (D-072) so the row is committed
even when the surrounding request later fails.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    delete,
    func,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base, get_session_factory, independent_session
from src.modules.ai_gateway.cost_log import COUNTED_OUTCOMES, CostEntry, CostOutcome


class AICostLogRow(Base):
    """Metadata of every gateway call — **no prompt / output text column** (guarded by
    ``test_cost_log_has_no_text_column``)."""

    __tablename__ = "ai_cost_log"
    __table_args__ = (
        Index("ix_ai_cost_log_brand_created", "brand_id", text("created_at DESC")),
        Index("ix_ai_cost_log_mode_created", "credential_mode", "created_at"),
        Index(
            "ix_ai_cost_log_pending",
            "created_at",
            postgresql_where=text("outcome = 'pending'"),
        ),
        CheckConstraint(
            "outcome IN ('pending', 'ok', 'error', 'ambiguous')", name="ck_ai_cost_log_outcome"
        ),
        CheckConstraint(
            "(outcome = 'error') = (error_category IS NOT NULL)",
            name="ck_ai_cost_log_error_iff_category",
        ),
        CheckConstraint(
            "credential_mode IN ('platform', 'user_key')", name="ck_ai_cost_log_credential_mode"
        ),
        CheckConstraint(
            "(prompt_tokens IS NULL OR prompt_tokens >= 0) AND "
            "(completion_tokens IS NULL OR completion_tokens >= 0) AND latency_ms >= 0",
            name="ck_ai_cost_log_non_negative",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    brand_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="SET NULL"), nullable=True
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    credential_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    error_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


def _tokens_expr():  # noqa: ANN202 - SQLAlchemy column expression
    return func.coalesce(AICostLogRow.prompt_tokens, 0) + func.coalesce(
        AICostLogRow.completion_tokens, 0
    )


class PgCostLogWriter:
    """D-072: every write is committed on its own session."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._session_factory = session_factory

    def _factory(self) -> async_sessionmaker[AsyncSession]:
        return self._session_factory or get_session_factory()

    async def reserve(self, entry: CostEntry) -> uuid.UUID:
        async with independent_session(self._factory()) as session:
            session.add(
                AICostLogRow(
                    id=entry.id,
                    brand_id=entry.brand_id,
                    purpose=entry.purpose,
                    provider=entry.provider,
                    model=entry.model,
                    credential_mode=entry.credential_mode,
                    prompt_tokens=entry.prompt_tokens,
                    completion_tokens=entry.completion_tokens,
                    estimated_cost_usd=entry.estimated_cost_usd,
                    latency_ms=entry.latency_ms,
                    prompt_version=entry.prompt_version,
                    outcome=entry.outcome.value,
                    error_category=entry.error_category,
                    created_at=entry.created_at,
                    settled_at=entry.settled_at,
                    request_id=entry.request_id,
                )
            )
            await session.flush()
        return entry.id

    async def settle(
        self,
        entry_id: uuid.UUID,
        *,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        estimated_cost_usd: Decimal | None,
        latency_ms: int,
        outcome: CostOutcome,
        error_category: str | None,
    ) -> None:
        async with independent_session(self._factory()) as session:
            await session.execute(
                update(AICostLogRow)
                .where(AICostLogRow.id == entry_id)
                .values(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    estimated_cost_usd=estimated_cost_usd,
                    latency_ms=max(0, latency_ms),
                    outcome=outcome.value,
                    error_category=error_category,
                    settled_at=datetime.now(tz=UTC),
                )
            )

    async def refund(self, entry_id: uuid.UUID) -> None:
        async with independent_session(self._factory()) as session:
            await session.execute(
                delete(AICostLogRow).where(
                    AICostLogRow.id == entry_id, AICostLogRow.outcome == CostOutcome.PENDING.value
                )
            )

    async def platform_usd_since(self, since: datetime) -> Decimal:
        async with independent_session(self._factory()) as session:
            value = await session.scalar(
                select(func.coalesce(func.sum(AICostLogRow.estimated_cost_usd), 0)).where(
                    AICostLogRow.credential_mode == "platform",
                    AICostLogRow.created_at >= since,
                    AICostLogRow.outcome.in_(COUNTED_OUTCOMES),
                )
            )
        return Decimal(value or 0)

    async def brand_tokens_since(self, brand_id: uuid.UUID, since: datetime) -> int:
        async with independent_session(self._factory()) as session:
            value = await session.scalar(
                select(func.coalesce(func.sum(_tokens_expr()), 0)).where(
                    AICostLogRow.brand_id == brand_id,
                    AICostLogRow.created_at >= since,
                    AICostLogRow.outcome.in_(COUNTED_OUTCOMES),
                )
            )
        return int(value or 0)

    async def settle_stale(self, older_than: datetime) -> int:
        """``pending`` ⇒ ``ambiguous``; the conservative ``estimated_cost_usd`` written at
        reservation time (``price × (prompt_estimate + max_tokens)``) is kept as-is."""
        async with independent_session(self._factory()) as session:
            result = await session.execute(
                update(AICostLogRow)
                .where(
                    AICostLogRow.outcome == CostOutcome.PENDING.value,
                    AICostLogRow.created_at < older_than,
                )
                .values(outcome=CostOutcome.AMBIGUOUS.value, settled_at=datetime.now(tz=UTC))
            )
        return int(result.rowcount or 0)


__all__ = ["AICostLogRow", "PgCostLogWriter"]
