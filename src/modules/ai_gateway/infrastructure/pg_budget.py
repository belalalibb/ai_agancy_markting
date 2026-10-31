"""PostgreSQL :class:`BudgetUsageReader` — the one ``UNION ALL`` over every AI feature.

Sources (``prompt_tokens`` / ``completion_tokens`` read from the private provenance):

* ``content_revisions.provenance`` where ``kind = 'ai_generated'`` (SPRINT-07);
* ``wa_reply_drafts.provenance`` (SPRINT-09 — every draft is AI generated).

Brand Brain calls are added in B-1 through ``ai_cost_log``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from src.modules.content.infrastructure.orm import ContentItemRow, ContentRevisionRow
from src.modules.whatsapp.infrastructure.orm import ReplyDraftRow


def _tokens(prov: Any) -> Any:  # noqa: ANN401 - SQLAlchemy JSONB column expression
    return func.coalesce(prov["prompt_tokens"].astext.cast(Integer), 0) + func.coalesce(
        prov["completion_tokens"].astext.cast(Integer), 0
    )


def ai_token_usage_sources(brand_id: UUID, since: datetime) -> Select:
    """``SELECT tokens, created_at FROM <revisions> UNION ALL <reply drafts>``."""
    revisions = (
        select(
            _tokens(ContentRevisionRow.provenance).label("tokens"),
            ContentRevisionRow.created_at.label("created_at"),
        )
        .select_from(ContentRevisionRow)
        .join(ContentItemRow, ContentItemRow.id == ContentRevisionRow.item_id)
        .where(
            ContentItemRow.brand_id == brand_id,
            ContentRevisionRow.created_at >= since,
            ContentRevisionRow.provenance["kind"].astext == "ai_generated",
        )
    )
    drafts = select(
        _tokens(ReplyDraftRow.provenance).label("tokens"),
        ReplyDraftRow.created_at.label("created_at"),
    ).where(ReplyDraftRow.brand_id == brand_id, ReplyDraftRow.created_at >= since)
    return revisions.union_all(drafts)


class PgBudgetUsageReader:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def combined_usage_since(self, brand_id: UUID, since: datetime) -> int:
        usage = ai_token_usage_sources(brand_id, since).subquery("ai_usage")
        value = await self._session.scalar(select(func.coalesce(func.sum(usage.c.tokens), 0)))
        return int(value or 0)

    async def oldest_generation_since(self, brand_id: UUID, since: datetime) -> datetime | None:
        usage = ai_token_usage_sources(brand_id, since).subquery("ai_usage")
        return await self._session.scalar(select(func.min(usage.c.created_at)))
