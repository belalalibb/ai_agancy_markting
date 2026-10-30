"""Persistence contract of the ``content`` module (D-013: Protocol here, PG impl in
``infrastructure``, fakes only in ``tests``).

SPRINT-07 Part A changes:

* **A-3** — ``next_revision_no`` is gone. Callers lock the aggregate with
  :meth:`ContentRepository.get_for_update` (``SELECT … FOR UPDATE``) and then call
  :meth:`ContentRepository.add_revision`, which assigns ``revision_no`` itself
  (``COALESCE(MAX(revision_no), 0) + 1`` inside the same transaction — safe under the lock).
* **A-4** — :meth:`ContentRepository.list_items` returns ``(item, current_body)`` pairs from
  one ``LEFT JOIN`` (no N+1).
* **A-5** — :meth:`ContentRepository.ai_token_usage_since` sums the private token counters of
  ``ai_generated`` revisions for the daily budget.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from src.modules.content.domain.models import (
    ApprovalEvent,
    ContentItem,
    ContentRevision,
    ContentStatus,
)


@runtime_checkable
class ContentRepository(Protocol):
    async def create_item(self, item: ContentItem, revision: ContentRevision) -> ContentItem:
        """Insert the item **and** its first revision (``revision_no`` 1 — a brand-new
        aggregate cannot race); return the item with ``current_revision_id`` set."""
        ...

    async def get_item(self, brand_id: UUID, item_id: UUID) -> ContentItem | None: ...

    async def get_for_update(self, brand_id: UUID, item_id: UUID) -> ContentItem | None:
        """A-3: read the item **and lock its row** (``SELECT … FOR UPDATE``) for the rest of
        the transaction so revision numbering and the status CAS that follow are serialised
        per aggregate. The in-memory fake serialises with an ``asyncio.Lock`` per item."""
        ...

    async def list_items(
        self,
        brand_id: UUID,
        *,
        status: ContentStatus | None,
        limit: int,
        cursor: tuple[datetime, UUID] | None,
    ) -> list[tuple[ContentItem, str | None]]:
        """Keyset page ordered by ``(created_at DESC, id DESC)``; ``cursor`` = last seen pair.
        Returns up to ``limit + 1`` ``(item, current_body)`` rows (A-4: one ``LEFT JOIN`` on
        ``current_revision_id``) so the caller can derive ``has_more``."""
        ...

    async def add_revision(
        self,
        item_id: UUID,
        *,
        body: str,
        provenance: dict[str, Any],
        created_by_tenant_id: UUID,
        created_at: datetime,
        media_refs: list[dict[str, Any]] | None = None,
    ) -> ContentRevision:
        """Insert the next revision (``revision_no`` assigned here — the caller must hold the
        aggregate lock from :meth:`get_for_update`) and point ``current_revision_id`` at it."""
        ...

    async def list_revisions(self, item_id: UUID, *, limit: int) -> list[ContentRevision]:
        """Newest first."""
        ...

    async def add_event(self, event: ApprovalEvent) -> ApprovalEvent: ...

    async def list_events(self, item_id: UUID) -> list[ApprovalEvent]:
        """Oldest first."""
        ...

    async def set_target_page(self, item_id: UUID, target_page_id: str | None) -> None:
        """Pin the destination Page (set/overridden by ``schedule``)."""
        ...

    async def compare_and_set_status(
        self,
        item_id: UUID,
        *,
        expected: ContentStatus,
        new: ContentStatus,
        scheduled_for: datetime | None,
        updated_at: datetime,
    ) -> bool:
        """B3 optimistic concurrency: ``UPDATE … WHERE id = :id AND status = :expected``;
        ``True`` iff exactly one row changed."""
        ...

    async def ai_token_usage_since(self, brand_id: UUID, since: datetime) -> int:
        """A-5: ``SUM(prompt_tokens + completion_tokens)`` over the ``ai_generated`` revisions
        of this brand created at or after ``since`` (``human`` rows are never counted)."""
        ...

    async def oldest_ai_generation_at(self, brand_id: UUID, since: datetime) -> datetime | None:
        """A-5 (429 path only): ``created_at`` of the oldest ``ai_generated`` revision in the
        window — tells the client when the budget starts to free up."""
        ...
