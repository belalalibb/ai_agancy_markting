"""Public request/response schemas of ``content`` (SPRINT-06 B3).

Zero-Secret by construction: nothing here ever carries a token. ``ContentItemPublic`` is
the same for every role; the detail view adds revisions (last 20) and the approval log.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.modules.content.domain.models import (
    ApprovalEvent,
    ContentItem,
    ContentRevision,
)

ChannelLiteral = Literal["facebook_page", "instagram"]
StatusLiteral = Literal[
    "draft", "pending_approval", "approved", "rejected", "scheduled", "archived"
]


class ContentItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: ChannelLiteral
    body: str = Field(min_length=1, max_length=5000)
    target_page_id: str | None = Field(default=None, min_length=1, max_length=64)


class ContentBodyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=5000)


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(min_length=3, max_length=1000)


class ApprovalNoteRequest(BaseModel):
    """Optional note for submit / approve / withdraw."""

    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=1000)


class ScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheduled_for: datetime
    target_page_id: str | None = Field(default=None, min_length=1, max_length=64)


class ContentRevisionPublic(BaseModel):
    id: UUID
    revision_no: int
    body: str
    media_refs: list[dict[str, Any]] = Field(default_factory=list)
    provenance: dict[str, Any]
    created_by_tenant_id: UUID
    created_at: datetime

    @classmethod
    def from_domain(cls, revision: ContentRevision) -> ContentRevisionPublic:
        return cls.model_validate(revision.model_dump(exclude={"item_id"}))


class ApprovalEventPublic(BaseModel):
    id: UUID
    revision_id: UUID
    action: str
    actor_tenant_id: UUID
    actor_user_id: UUID
    note: str | None = None
    created_at: datetime

    @classmethod
    def from_domain(cls, event: ApprovalEvent) -> ApprovalEventPublic:
        return cls.model_validate(event.model_dump(exclude={"item_id"}))


class ContentItemPublic(BaseModel):
    id: UUID
    brand_id: UUID
    created_by_tenant_id: UUID
    channel: str
    target_page_id: str | None = None
    status: str
    current_revision_id: UUID | None = None
    scheduled_for: datetime | None = None
    created_at: datetime
    updated_at: datetime
    current_body: str | None = Field(
        default=None, description="Body of the current revision (convenience for lists)."
    )

    @classmethod
    def from_domain(
        cls, item: ContentItem, *, current_body: str | None = None
    ) -> ContentItemPublic:
        return cls.model_validate({**item.model_dump(), "current_body": current_body})


class ContentItemDetail(ContentItemPublic):
    revisions: list[ContentRevisionPublic] = Field(default_factory=list)
    approval_events: list[ApprovalEventPublic] = Field(default_factory=list)
    # SPRINT-06 B-2: token usage of the AI generations behind this item — OWNER/MANAGER only;
    # the router strips the field for VIEWER (visibility table §2-0 b).
    generation_log: list[dict[str, Any]] | None = None


class ContentItemPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ContentItemPublic] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False
