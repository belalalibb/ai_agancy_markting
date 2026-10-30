"""PostgreSQL implementation of :class:`ContentRepository` (SQLAlchemy 2 async).

Status changes go through ``compare_and_set_status`` — a single ``UPDATE … WHERE status =
:expected`` whose ``rowcount`` decides the race (B3 optimistic concurrency; B3-I1).
"""

from __future__ import annotations

from datetime import datetime
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
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
        )
        await self._session.flush()
        await self.add_revision(revision)
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

    async def list_items(
        self,
        brand_id: UUID,
        *,
        status: ContentStatus | None,
        limit: int,
        cursor: tuple[datetime, UUID] | None,
    ) -> list[ContentItem]:
        stmt = select(ContentItemRow).where(ContentItemRow.brand_id == brand_id)
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
        rows = (await self._session.scalars(stmt)).all()
        return [_item_to_domain(r) for r in rows]

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
    ) -> bool:
        result = await self._session.execute(
            update(ContentItemRow)
            .where(ContentItemRow.id == item_id, ContentItemRow.status == expected.value)
            .values(status=new.value, scheduled_for=scheduled_for, updated_at=updated_at)
        )
        return (result.rowcount or 0) == 1

    # -------------------------------------------------------- revisions ---
    async def next_revision_no(self, item_id: UUID) -> int:
        current = await self._session.scalar(
            select(func.max(ContentRevisionRow.revision_no)).where(
                ContentRevisionRow.item_id == item_id
            )
        )
        return int(current or 0) + 1

    async def add_revision(self, revision: ContentRevision) -> ContentRevision:
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
