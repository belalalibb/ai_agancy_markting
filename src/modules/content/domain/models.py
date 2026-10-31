"""Domain models of the ``content`` module (SPRINT-06 B1/B2, SPRINT-07 A-1/A-2).

``ContentItem`` is the aggregate root (one post-to-be per brand and channel), its text lives
in immutable ``ContentRevision`` rows and every approval decision is an ``ApprovalEvent``
(append-only audit, D-055). All timestamps are timezone-aware UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class Channel(StrEnum):
    FACEBOOK_PAGE = "facebook_page"
    INSTAGRAM = "instagram"


# SPRINT-07 A-2: single source of the per-channel hard length limits (service + prompts).
CHANNEL_LIMITS: Final[dict[Channel, int]] = {Channel.INSTAGRAM: 2200, Channel.FACEBOOK_PAGE: 5000}

# SPRINT-07 A-1 (D-066): the only provenance keys every role may see. Everything else
# (provider, model, prompt_hash, tokens, latency, credential_mode, truncated) is **private**
# and surfaces solely in ``generation_log`` for OWNER/MANAGER. Storage is one JSONB dict.
PUBLIC_PROVENANCE_KEYS: Final[frozenset[str]] = frozenset(
    {"kind", "generated_at", "prompt_version"}
)


class ContentStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    SCHEDULED = "scheduled"
    ARCHIVED = "archived"
    # SPRINT-07 B2: terminal outcomes of the scheduler worker (system transitions only).
    PUBLISHED = "published"
    PUBLISH_FAILED = "publish_failed"


def public_provenance(provenance: dict[str, Any]) -> dict[str, Any]:
    """A-1: project a stored provenance dict onto :data:`PUBLIC_PROVENANCE_KEYS`."""
    return {k: v for k, v in provenance.items() if k in PUBLIC_PROVENANCE_KEYS}


class ApprovalAction(StrEnum):
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    # SPRINT-07 B2/B7 — publishing audit trail (D-055).
    SCHEDULED = "scheduled"  # human: schedule / publish-retry (the authorisation record)
    PUBLISHED = "published"  # system: scheduler worker, no actor
    PUBLISH_FAILED = "publish_failed"  # system: scheduler worker, no actor
    PUBLISH_CANCELLED = "publish_cancelled"  # human: unschedule / archive of a scheduled item


# Events the scheduler worker writes outside any user request ⇒ ``actor_*`` may be NULL
# (``ck_approval_events_actor_required`` in migration 0006 enforces exactly this set).
SYSTEM_ACTIONS: Final[frozenset[ApprovalAction]] = frozenset(
    {ApprovalAction.PUBLISHED, ApprovalAction.PUBLISH_FAILED}
)


class ContentRevision(BaseModel):
    """One immutable version of the text. ``provenance`` is the D-055 evidence record:
    ``{"kind": "human"}`` or ``{"kind": "ai_generated", "provider", "model", "prompt_hash",
    "generated_at"}``."""

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    item_id: UUID
    revision_no: int = Field(ge=1)
    body: str = Field(min_length=1, max_length=5000)
    media_refs: list[dict[str, Any]] = Field(default_factory=list)
    provenance: dict[str, Any]
    created_by_tenant_id: UUID
    created_at: datetime = Field(default_factory=_utcnow)


class ApprovalEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    item_id: UUID
    revision_id: UUID
    action: ApprovalAction
    # ``None`` only for :data:`SYSTEM_ACTIONS` (worker events) — validated below.
    actor_tenant_id: UUID | None
    actor_user_id: UUID | None
    note: str | None = Field(default=None, max_length=1000)
    created_at: datetime = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def _actor_required_unless_system(self) -> ApprovalEvent:
        if self.action not in SYSTEM_ACTIONS and (
            self.actor_tenant_id is None or self.actor_user_id is None
        ):
            raise ValueError(f"action {self.action.value!r} requires an actor")
        return self


class ContentItem(BaseModel):
    """Aggregate root. ``current_revision_id`` points at the latest revision; ``scheduled_for``
    is set **iff** ``status == scheduled`` and ``published_at`` **iff** ``status == published``
    (DB CHECKs mirror both)."""

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    brand_id: UUID
    created_by_tenant_id: UUID
    channel: Channel
    target_page_id: str | None = Field(default=None, max_length=64)
    status: ContentStatus = ContentStatus.DRAFT
    current_revision_id: UUID | None = None
    scheduled_for: datetime | None = None
    published_at: datetime | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
