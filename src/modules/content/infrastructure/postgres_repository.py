"""PostgreSQL implementation of :class:`ContentRepository` (SQLAlchemy 2 async).

Status changes go through ``compare_and_set_status`` — a single ``UPDATE … WHERE status =
:expected`` whose ``rowcount`` decides the race (B3 optimistic concurrency; B3-I1).

SPRINT-07 A-3: revision numbering happens **after** ``get_for_update`` locked the item row
(``SELECT … FOR UPDATE``), so ``COALESCE(MAX(revision_no), 0) + 1`` inside the same
transaction can no longer collide (A3-I1 — no ``IntegrityError``, no 500).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.content.domain.models import (
    ApprovalAction,
    ApprovalEvent,
    Channel,
    ContentItem,
    ContentRevision,
    ContentStatus,
)
from src.modules.content.infrastructure.orm import (
    ApprovalEventRow,
    ContentItemRow,
    ContentRevisionRow,
)


class PostgresContentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------ items ---
    async def create_item(self, item: ContentItem, revision: ContentRevision) -> ContentItem:
        self._session.add(
            ContentItemRow(
                id=item.id,
                brand_id=item.brand_id,
                created_by_tenant_id=item.created_by_tenant_id,
                channel=item.channel.value,
                target_page_id=item.target_page_id,
                status=item.status.value,
                current_revision_id=None,
                scheduled_for=item.scheduled_for,
                published_at=item.published_at,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
        )
        await self._session.flush()
        await self._insert_revision(revision)
        stored = await self.get_item(item.brand_id, item.id)
        assert stored is not None  # noqa: S101 - just inserted in this transaction
        return stored

    async def get_item(self, brand_id: UUID, item_id: UUID) -> ContentItem | None:
        row = await self._session.scalar(
            select(ContentItemRow).where(
                ContentItemRow.brand_id == brand_id, ContentItemRow.id == item_id
            )
        )
        return _item_to_domain(row) if row is not None else None

    async def get_for_update(self, brand_id: UUID, item_id: UUID) -> ContentItem | None:
        """A-3: lock the aggregate row for the rest of the transaction."""
        row = await self._session.scalar(
            select(ContentItemRow)
            .where(ContentItemRow.brand_id == brand_id, ContentItemRow.id == item_id)
            .with_for_update()
        )
        return _item_to_domain(row) if row is not None else None

    async def list_items(
        self,
        brand_id: UUID,
        *,
        status: ContentStatus | None,
        limit: int,
        cursor: tuple[datetime, UUID] | None,
    ) -> list[tuple[ContentItem, str | None]]:
        # A-4: one query — the current body comes from a LEFT JOIN on current_revision_id.
        stmt = (
            select(ContentItemRow, ContentRevisionRow.body)
            .outerjoin(
                ContentRevisionRow, ContentRevisionRow.id == ContentItemRow.current_revision_id
            )
            .where(ContentItemRow.brand_id == brand_id)
        )
        if status is not None:
            stmt = stmt.where(ContentItemRow.status == status.value)
        if cursor is not None:
            created_at, last_id = cursor
            stmt = stmt.where(
                or_(
                    ContentItemRow.created_at < created_at,
                    and_(ContentItemRow.created_at == created_at, ContentItemRow.id < last_id),
                )
            )
        stmt = stmt.order_by(ContentItemRow.created_at.desc(), ContentItemRow.id.desc()).limit(
            limit + 1
        )
        rows = (await self._session.execute(stmt)).all()
        return [(_item_to_domain(row), body) for row, body in rows]

    async def set_target_page(self, item_id: UUID, target_page_id: str | None) -> None:
        await self._session.execute(
            update(ContentItemRow)
            .where(ContentItemRow.id == item_id)
            .values(target_page_id=target_page_id)
        )

    async def compare_and_set_status(
        self,
        item_id: UUID,
        *,
        expected: ContentStatus,
        new: ContentStatus,
        scheduled_for: datetime | None,
        updated_at: datetime,
        published_at: datetime | None = None,
    ) -> bool:
        # ``published_at`` is set iff ``new == published`` (DB CHECK) — the caller passes it
        # for the system transition; every other move clears it unless archiving a
        # published post (``keep``: the row's current value is preserved).
        values: dict[str, object] = {
            "status": new.value,
            "scheduled_for": scheduled_for,
            "updated_at": updated_at,
        }
        if new is ContentStatus.PUBLISHED:
            values["published_at"] = published_at
        elif not (expected is ContentStatus.PUBLISHED and new is ContentStatus.ARCHIVED):
            values["published_at"] = None
        result = await self._session.execute(
            update(ContentItemRow)
            .where(ContentItemRow.id == item_id, ContentItemRow.status == expected.value)
            .values(**values)
        )
        return (result.rowcount or 0) == 1

    # -------------------------------------------------------- revisions ---
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
        """A-3: ``revision_no`` is computed here, under the caller's row lock.

        SPRINT-09 A-3 (F3): ``media_refs=None`` means *keep* the current revision's refs
        (copied here); ``[]`` means *remove* them explicitly.
        """
        current = await self._session.scalar(
            select(func.coalesce(func.max(ContentRevisionRow.revision_no), 0)).where(
                ContentRevisionRow.item_id == item_id
            )
        )
        if media_refs is None:
            media_refs = await self._current_media_refs(item_id)
        revision = ContentRevision(
            item_id=item_id,
            revision_no=int(current or 0) + 1,
            body=body,
            media_refs=list(media_refs),
            provenance=provenance,
            created_by_tenant_id=created_by_tenant_id,
            created_at=created_at,
        )
        return await self._insert_revision(revision)

    async def _current_media_refs(self, item_id: UUID) -> list[dict[str, Any]]:
        refs = await self._session.scalar(
            select(ContentRevisionRow.media_refs)
            .join(ContentItemRow, ContentItemRow.current_revision_id == ContentRevisionRow.id)
            .where(ContentItemRow.id == item_id)
        )
        return list(refs or [])

    async def _insert_revision(self, revision: ContentRevision) -> ContentRevision:
        self._session.add(
            ContentRevisionRow(
                id=revision.id,
                item_id=revision.item_id,
                revision_no=revision.revision_no,
                body=revision.body,
                media_refs=list(revision.media_refs),
                provenance=dict(revision.provenance),
                created_by_tenant_id=revision.created_by_tenant_id,
                created_at=revision.created_at,
            )
        )
        await self._session.flush()
        await self._session.execute(
            update(ContentItemRow)
            .where(ContentItemRow.id == revision.item_id)
            .values(current_revision_id=revision.id, updated_at=revision.created_at)
        )
        return revision

    async def list_revisions(self, item_id: UUID, *, limit: int) -> list[ContentRevision]:
        rows = (
            await self._session.scalars(
                select(ContentRevisionRow)
                .where(ContentRevisionRow.item_id == item_id)
                .order_by(ContentRevisionRow.revision_no.desc())
                .limit(limit)
            )
        ).all()
        return [_revision_to_domain(r) for r in rows]

    async def get_revision(self, revision_id: UUID) -> ContentRevision | None:
        row = await self._session.get(ContentRevisionRow, revision_id)
        return _revision_to_domain(row) if row is not None else None

    # ----------------------------------------------------------- events ---
    async def add_event(self, event: ApprovalEvent) -> ApprovalEvent:
        self._session.add(
            ApprovalEventRow(
                id=event.id,
                item_id=event.item_id,
                revision_id=event.revision_id,
                action=event.action.value,
                actor_tenant_id=event.actor_tenant_id,
                actor_user_id=event.actor_user_id,
                note=event.note,
                created_at=event.created_at,
            )
        )
        await self._session.flush()
        return event

    async def list_events(self, item_id: UUID) -> list[ApprovalEvent]:
        rows = (
            await self._session.scalars(
                select(ApprovalEventRow)
                .where(ApprovalEventRow.item_id == item_id)
                .order_by(ApprovalEventRow.created_at.asc(), ApprovalEventRow.id.asc())
            )
        ).all()
        return [_event_to_domain(r) for r in rows]


def _item_to_domain(row: ContentItemRow) -> ContentItem:
    return ContentItem(
        id=row.id,
        brand_id=row.brand_id,
        created_by_tenant_id=row.created_by_tenant_id,
        channel=Channel(row.channel),
        target_page_id=row.target_page_id,
        status=ContentStatus(row.status),
        current_revision_id=row.current_revision_id,
        scheduled_for=row.scheduled_for,
        published_at=row.published_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _revision_to_domain(row: ContentRevisionRow) -> ContentRevision:
    return ContentRevision(
        id=row.id,
        item_id=row.item_id,
        revision_no=row.revision_no,
        body=row.body,
        media_refs=list(row.media_refs or []),
        provenance=dict(row.provenance),
        created_by_tenant_id=row.created_by_tenant_id,
        created_at=row.created_at,
    )


def _event_to_domain(row: ApprovalEventRow) -> ApprovalEvent:
    return ApprovalEvent(
        id=row.id,
        item_id=row.item_id,
        revision_id=row.revision_id,
        action=ApprovalAction(row.action),
        actor_tenant_id=row.actor_tenant_id,
        actor_user_id=row.actor_user_id,
        note=row.note,
        created_at=row.created_at,
    )
