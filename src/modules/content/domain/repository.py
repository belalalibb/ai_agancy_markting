"""Persistence contract of the ``content`` module (D-013: Protocol here, PG impl in
``infrastructure``, fakes only in ``tests``)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable
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
        """Insert the item **and** its first revision; return the item with
        ``current_revision_id`` set."""
        ...

    async def get_item(self, brand_id: UUID, item_id: UUID) -> ContentItem | None: ...

    async def list_items(
        self,
        brand_id: UUID,
        *,
        status: ContentStatus | None,
        limit: int,
        cursor: tuple[datetime, UUID] | None,
    ) -> list[ContentItem]:
        """Keyset page ordered by ``(created_at DESC, id DESC)``; ``cursor`` = last seen pair.
        Returns up to ``limit + 1`` rows so the caller can derive ``has_more``."""
        ...

    async def next_revision_no(self, item_id: UUID) -> int: ...

    async def add_revision(self, revision: ContentRevision) -> ContentRevision:
        """Insert a revision and point ``current_revision_id`` at it."""
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
