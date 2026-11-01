"""PostgreSQL :class:`InternalMetricsReader` — the five ``measured/internal_db`` metrics of
``EvidenceCollector.collect_internal`` (B-1), read from tables other modules own:

* ``wa_conversations_7d``        — ``wa_conversations`` with an inbound in the last 7 days;
* ``wa_approval_rate_30d``       — ``wa_learning_signals``: approved (unchanged + after edit)
                                   ÷ decided (approved + rejected) in 30 days;
* ``content_approval_rate_30d``  — ``approval_events``: approved ÷ (approved + rejected);
* ``posting_frequency_7d``       — ``publish_jobs`` with ``status = 'succeeded'`` in 7 days;
* ``avg_reply_latency_s``        — ``wa_messages``: mean seconds from an inbound message to
                                   the next outbound one in the same conversation (30 days).

Read-only; nothing here writes.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from src.modules.brand_brain.service import InternalMetrics
from src.modules.content.infrastructure.orm import ApprovalEventRow, ContentItemRow
from src.modules.publishing.infrastructure.orm import PublishJobRow
from src.modules.whatsapp.infrastructure.orm import ConversationRow, LearningSignalRow, MessageRow


class PgInternalMetricsReader:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def read(self, brand_id: UUID, *, now: datetime) -> InternalMetrics:
        since_7 = now - timedelta(days=7)
        since_30 = now - timedelta(days=30)

        conversations = await self._session.scalar(
            select(func.count())
            .select_from(ConversationRow)
            .where(
                ConversationRow.brand_id == brand_id, ConversationRow.last_inbound_at >= since_7
            )
        )

        approved = func.sum(
            case(
                (LearningSignalRow.signal.in_(("approved_unchanged", "approved_after_edit")), 1),
                else_=0,
            )
        )
        decided = func.sum(
            case(
                (
                    LearningSignalRow.signal.in_(
                        ("approved_unchanged", "approved_after_edit", "rejected")
                    ),
                    1,
                ),
                else_=0,
            )
        )
        wa_row = (
            await self._session.execute(
                select(approved, decided).where(
                    LearningSignalRow.brand_id == brand_id,
                    LearningSignalRow.created_at >= since_30,
                )
            )
        ).one()
        wa_approved, wa_decided = int(wa_row[0] or 0), int(wa_row[1] or 0)

        c_approved = func.sum(case((ApprovalEventRow.action == "approved", 1), else_=0))
        c_decided = func.sum(
            case((ApprovalEventRow.action.in_(("approved", "rejected")), 1), else_=0)
        )
        content_row = (
            await self._session.execute(
                select(c_approved, c_decided)
                .select_from(ApprovalEventRow)
                .join(ContentItemRow, ContentItemRow.id == ApprovalEventRow.item_id)
                .where(
                    ContentItemRow.brand_id == brand_id, ApprovalEventRow.created_at >= since_30
                )
            )
        ).one()
        content_approved, content_decided = int(content_row[0] or 0), int(content_row[1] or 0)

        published = await self._session.scalar(
            select(func.count())
            .select_from(PublishJobRow)
            .where(
                PublishJobRow.brand_id == brand_id,
                PublishJobRow.status == "succeeded",
                PublishJobRow.updated_at >= since_7,
            )
        )

        inbound = aliased(MessageRow)
        outbound = aliased(MessageRow)
        first_reply = (
            select(func.min(outbound.created_at))
            .where(
                outbound.conversation_id == inbound.conversation_id,
                outbound.direction == "outbound",
                outbound.created_at > inbound.created_at,
            )
            .correlate(inbound)
            .scalar_subquery()
        )
        latency = func.extract("epoch", first_reply - inbound.created_at)
        latency_row = (
            await self._session.execute(
                select(func.avg(latency), func.count(latency)).where(
                    inbound.brand_id == brand_id,
                    inbound.direction == "inbound",
                    inbound.created_at >= since_30,
                )
            )
        ).one()
        avg_latency = float(latency_row[0]) if latency_row[0] is not None else None

        return InternalMetrics(
            wa_conversations_7d=int(conversations or 0),
            wa_approval_rate_30d=(wa_approved / wa_decided) if wa_decided else None,
            content_approval_rate_30d=(
                (content_approved / content_decided) if content_decided else None
            ),
            posting_frequency_7d=int(published or 0),
            avg_reply_latency_s=avg_latency,
            wa_decisions_30d=wa_decided,
            content_decisions_30d=content_decided,
            reply_samples=int(latency_row[1] or 0),
        )
