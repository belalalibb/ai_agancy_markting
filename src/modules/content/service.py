"""Use-case layer of ``content`` (SPRINT-06 B2/B3).

All status changes go: pure :func:`transition` ⇒ ``compare_and_set_status`` (optimistic;
``False`` ⇒ ``InvalidTransitionError`` 409 because somebody else moved the item first) ⇒
``ApprovalEvent`` where the action is part of the approval loop.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from src.modules.content.domain.lifecycle import (
    EDIT_RESETS_TO_DRAFT,
    Action,
    LifecycleError,
    transition,
)
from src.modules.content.domain.models import (
    ApprovalAction,
    ApprovalEvent,
    Channel,
    ContentItem,
    ContentRevision,
    ContentStatus,
)
from src.modules.content.domain.repository import ContentRepository
from src.modules.content.exceptions import (
    ApprovalRequiresOwnerError,
    ContentInsufficientRoleError,
    ContentNotFoundError,
    InstagramNotLinkedError,
    InvalidTransitionError,
    RejectNoteRequiredError,
    ScheduleTooSoonError,
    TargetPageNotSelectedError,
)
from src.modules.social_connect.domain.models import AccountStatus
from src.modules.social_connect.domain.repository import ConnectedAccountRepository
from src.modules.tenancy.domain.access import BrandRole

logger = logging.getLogger(__name__)

SCHEDULE_MIN_LEAD = timedelta(minutes=5)
REJECT_NOTE_MIN_CHARS = 3
REVISIONS_IN_DETAIL = 20

_EVENT_FOR_ACTION: dict[Action, ApprovalAction] = {
    Action.SUBMIT: ApprovalAction.SUBMITTED,
    Action.APPROVE: ApprovalAction.APPROVED,
    Action.REJECT: ApprovalAction.REJECTED,
    Action.WITHDRAW: ApprovalAction.WITHDRAWN,
}


class ContentService:
    def __init__(
        self,
        repository: ContentRepository,
        connections: ConnectedAccountRepository,
    ) -> None:
        self._repo = repository
        self._connections = connections

    # ------------------------------------------------------------ create ---
    async def create_item(
        self,
        brand_id: UUID,
        *,
        actor_tenant_id: UUID,
        channel: Channel,
        body: str,
        target_page_id: str | None,
    ) -> ContentItem:
        """Draft + revision 1 with ``provenance={"kind": "human"}``."""
        now = datetime.now(tz=UTC)
        item = ContentItem(
            brand_id=brand_id,
            created_by_tenant_id=actor_tenant_id,
            channel=channel,
            target_page_id=target_page_id,
            status=ContentStatus.DRAFT,
            created_at=now,
            updated_at=now,
        )
        revision = ContentRevision(
            item_id=item.id,
            revision_no=1,
            body=body,
            provenance={"kind": "human"},
            created_by_tenant_id=actor_tenant_id,
            created_at=now,
        )
        stored = await self._repo.create_item(item, revision)
        logger.info(
            "content item created: brand=%s item=%s channel=%s", brand_id, item.id, channel.value
        )
        return stored

    # -------------------------------------------------------------- reads ---
    async def get_item(self, brand_id: UUID, item_id: UUID) -> ContentItem:
        item = await self._repo.get_item(brand_id, item_id)
        if item is None:
            raise ContentNotFoundError()
        return item

    async def get_detail(
        self, brand_id: UUID, item_id: UUID
    ) -> tuple[ContentItem, list[ContentRevision], list[ApprovalEvent]]:
        item = await self.get_item(brand_id, item_id)
        revisions = await self._repo.list_revisions(item_id, limit=REVISIONS_IN_DETAIL)
        events = await self._repo.list_events(item_id)
        return item, revisions, events

    async def current_body(self, item: ContentItem) -> str | None:
        revisions = await self._repo.list_revisions(item.id, limit=1)
        return revisions[0].body if revisions else None

    async def list_items(
        self,
        brand_id: UUID,
        *,
        status: ContentStatus | None,
        limit: int,
        cursor: tuple[datetime, UUID] | None,
    ) -> tuple[list[ContentItem], bool]:
        rows = await self._repo.list_items(brand_id, status=status, limit=limit, cursor=cursor)
        has_more = len(rows) > limit
        return rows[:limit], has_more

    # ---------------------------------------------------------- body edit ---
    async def update_body(
        self,
        brand_id: UUID,
        item_id: UUID,
        *,
        body: str,
        actor_tenant_id: UUID,
        actor_user_id: UUID,
        provenance: dict[str, Any] | None = None,
    ) -> ContentItem:
        """New revision. From ``pending_approval``/``approved``/``rejected``/``scheduled`` the
        item returns to ``draft`` with an automatic ``withdrawn`` event (B2-U2). ``archived``
        items are immutable ⇒ 409 ``invalid_transition``."""
        item = await self.get_item(brand_id, item_id)
        if item.status is ContentStatus.ARCHIVED:
            raise InvalidTransitionError(item.status, "edit")
        now = datetime.now(tz=UTC)
        revision = ContentRevision(
            item_id=item.id,
            revision_no=await self._repo.next_revision_no(item.id),
            body=body,
            provenance=provenance or {"kind": "human"},
            created_by_tenant_id=actor_tenant_id,
            created_at=now,
        )
        if item.status in EDIT_RESETS_TO_DRAFT:
            moved = await self._repo.compare_and_set_status(
                item.id,
                expected=item.status,
                new=ContentStatus.DRAFT,
                scheduled_for=None,
                updated_at=now,
            )
            if not moved:
                raise InvalidTransitionError(item.status, "edit", "item changed concurrently")
            await self._repo.add_revision(revision)
            await self._repo.add_event(
                ApprovalEvent(
                    item_id=item.id,
                    revision_id=revision.id,
                    action=ApprovalAction.WITHDRAWN,
                    actor_tenant_id=actor_tenant_id,
                    actor_user_id=actor_user_id,
                    note="body edited — returned to draft",
                    created_at=now,
                )
            )
        else:
            await self._repo.add_revision(revision)
        return await self.get_item(brand_id, item_id)

    # --------------------------------------------------------- transitions ---
    async def act(
        self,
        brand_id: UUID,
        item_id: UUID,
        action: Action,
        *,
        actor_role: BrandRole,
        actor_tenant_id: UUID,
        actor_user_id: UUID,
        note: str | None = None,
        scheduled_for: datetime | None = None,
        target_page_id: str | None = None,
    ) -> ContentItem:
        item = await self.get_item(brand_id, item_id)
        now = datetime.now(tz=UTC)
        if action is Action.REJECT and (note is None or len(note.strip()) < REJECT_NOTE_MIN_CHARS):
            # Role first (a MANAGER gets 403, not 422), then the note rule.
            _raise_if_error(
                transition(
                    item,
                    action,
                    actor_role=actor_role,
                    actor_tenant_id=actor_tenant_id,
                    now=now,
                )
            )
            raise RejectNoteRequiredError()
        if action is Action.SCHEDULE:
            # Role + state before the (repository-backed) schedule checks.
            _raise_if_error(
                transition(
                    item,
                    action,
                    actor_role=actor_role,
                    actor_tenant_id=actor_tenant_id,
                    now=now,
                    scheduled_for=scheduled_for,
                )
            )
            page_id = target_page_id or item.target_page_id
            await self._validate_schedule(item, scheduled_for, page_id, now)
            if page_id != item.target_page_id:
                await self._repo.set_target_page(item.id, page_id)
        result = transition(
            item,
            action,
            actor_role=actor_role,
            actor_tenant_id=actor_tenant_id,
            now=now,
            scheduled_for=scheduled_for,
        )
        _raise_if_error(result)
        assert isinstance(result, ContentItem)  # noqa: S101 - narrowed by _raise_if_error
        moved = await self._repo.compare_and_set_status(
            item.id,
            expected=item.status,
            new=result.status,
            scheduled_for=result.scheduled_for,
            updated_at=now,
        )
        if not moved:
            raise InvalidTransitionError(item.status, action.value, "item changed concurrently")
        event_action = _EVENT_FOR_ACTION.get(action)
        if event_action is not None and item.current_revision_id is not None:
            await self._repo.add_event(
                ApprovalEvent(
                    item_id=item.id,
                    revision_id=item.current_revision_id,
                    action=event_action,
                    actor_tenant_id=actor_tenant_id,
                    actor_user_id=actor_user_id,
                    note=note,
                    created_at=now,
                )
            )
        logger.info(
            "content transition: brand=%s item=%s %s --%s--> %s by role=%s",
            brand_id,
            item.id,
            item.status.value,
            action.value,
            result.status.value,
            actor_role.value,
        )
        return await self.get_item(brand_id, item_id)

    async def _validate_schedule(
        self,
        item: ContentItem,
        scheduled_for: datetime | None,
        page_id: str | None,
        now: datetime,
    ) -> None:
        if scheduled_for is None or scheduled_for <= now + SCHEDULE_MIN_LEAD:
            raise ScheduleTooSoonError()
        if page_id is None:
            raise TargetPageNotSelectedError()
        accounts = await self._connections.list_by_brand(item.brand_id)
        page = None
        for account in accounts:
            if account.status is not AccountStatus.ACTIVE:
                continue
            for candidate in account.pages:
                if (
                    candidate.page_id == page_id
                    and candidate.is_selected
                    and candidate.encrypted_page_token
                ):
                    page = candidate
        if page is None:
            raise TargetPageNotSelectedError()
        if item.channel is Channel.INSTAGRAM and page.instagram is None:
            raise InstagramNotLinkedError()


def _raise_if_error(result: ContentItem | LifecycleError) -> None:
    if isinstance(result, LifecycleError):
        if result.error == "approval_requires_owner":
            raise ApprovalRequiresOwnerError(result.message)
        if result.error == "insufficient_role":
            raise ContentInsufficientRoleError(result.message)
        raise InvalidTransitionError(result.from_status, result.action.value, result.message)
