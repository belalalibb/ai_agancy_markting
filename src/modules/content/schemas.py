"""Public request/response schemas of ``content`` (SPRINT-06 B3, SPRINT-07 A-1/A-4).

Zero-Secret by construction: nothing here ever carries a token. ``ContentItemPublic`` is
the same for every role; the detail view adds revisions (last 20) and the approval log.

SPRINT-07 A-1 (D-066): ``ContentRevisionPublic.provenance`` is projected onto
:data:`PUBLIC_PROVENANCE_KEYS` (``kind``, ``generated_at``, ``prompt_version``) for every
role. The private fields (provider/model/prompt_hash/tokens/latency/credential_mode/
truncated) surface **only** through ``ContentItemDetailWithUsage.generation_log`` (A-4),
the model returned to OWNER/MANAGER; VIEWER receives ``ContentItemDetail`` without the key.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.modules.content.domain.models import (
    ApprovalEvent,
    Channel,
    ContentItem,
    ContentRevision,
    public_provenance,
)
from src.modules.publishing.domain.models import PublishAttempt, PublishJob

# SPRINT-08 B-1 (B1-U2): derived from the enum — ``Literal[("a", "b")]`` is ``Literal["a", "b"]``.
ChannelLiteral = Literal[tuple(c.value for c in Channel)]  # type: ignore[valid-type]
StatusLiteral = Literal[
    "draft",
    "pending_approval",
    "approved",
    "rejected",
    "scheduled",
    "published",
    "publish_failed",
    "archived",
]


class MediaRef(BaseModel):
    """SPRINT-07 B2: a link to an already-hosted image (no uploads in M4 — directive §1).
    The Instagram ``jpg|jpeg|png`` rule is applied at ``schedule`` time, not here."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=12, max_length=2048, pattern=r"^https?://")
    kind: Literal["image"] = "image"


class ContentItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: ChannelLiteral
    body: str = Field(min_length=1, max_length=5000)
    target_page_id: str | None = Field(default=None, min_length=1, max_length=64)
    media_refs: list[MediaRef] = Field(default_factory=list, max_length=1)


class ContentBodyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=5000)
    # SPRINT-09 A-3 (F3): omitted/``null`` ⇒ keep the current revision's refs; ``[]`` ⇒
    # remove them. (Explicit compat change from SPRINT-07, where omission meant ``[]``.)
    media_refs: list[MediaRef] | None = Field(default=None, max_length=1)


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


class GenerateRequest(BaseModel):
    """B5: ``POST /items/{id}/generate`` body."""

    model_config = ConfigDict(extra="forbid")

    brief: str = Field(min_length=10, max_length=2000)
    tone: Literal["professional", "friendly", "promotional"] = "professional"
    language: Literal["ar", "en"] = "ar"


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
        data = revision.model_dump(exclude={"item_id"})
        data["provenance"] = public_provenance(revision.provenance)  # A-1
        return cls.model_validate(data)


class ApprovalEventPublic(BaseModel):
    id: UUID
    revision_id: UUID
    action: str
    # ``None`` only for worker events (``published`` / ``publish_failed``, SPRINT-07 B1).
    actor_tenant_id: UUID | None = None
    actor_user_id: UUID | None = None
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
    published_at: datetime | None = None
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
    """Detail view for **every** role (no ``generation_log`` key at all — A-4)."""

    revisions: list[ContentRevisionPublic] = Field(default_factory=list)
    approval_events: list[ApprovalEventPublic] = Field(default_factory=list)


class GenerationLogEntry(BaseModel):
    """One AI generation behind the item — the **private** provenance fields (A-1/A-4)."""

    revision_no: int
    generated_at: datetime | None = None
    prompt_version: str | None = None
    provider: str | None = None
    model: str | None = None
    prompt_hash: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int | None = None
    credential_mode: str | None = None
    truncated: bool = False

    @classmethod
    def from_revision(cls, revision: ContentRevision) -> GenerationLogEntry:
        known = set(cls.model_fields) - {"revision_no"}
        data = {k: v for k, v in revision.provenance.items() if k in known}
        return cls.model_validate({**data, "revision_no": revision.revision_no})


class ContentItemDetailWithUsage(ContentItemDetail):
    """Detail view for OWNER/MANAGER: adds the generation log (visibility table §2-0 b)."""

    generation_log: list[GenerationLogEntry] = Field(default_factory=list)


class ContentItemPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ContentItemPublic] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False


# ---------------------------------------------------------- SPRINT-07 B7 publish status ---
class PublishAttemptPublic(BaseModel):
    """VIEWER projection: when it ran and how it ended — no error codes, no external ids."""

    attempt_no: int
    started_at: datetime
    finished_at: datetime | None = None
    outcome: str | None = None

    @classmethod
    def from_domain(cls, attempt: PublishAttempt) -> PublishAttemptPublic:
        return cls.model_validate(attempt.model_dump(include=set(cls.model_fields)))


class PublishAttemptDetail(PublishAttemptPublic):
    """OWNER/MANAGER projection: adds the internal error class and Meta's codes/trace id
    (never Meta's message text)."""

    error_class: str | None = None
    graph_error_code: int | None = None
    graph_error_subcode: int | None = None
    graph_trace_id: str | None = None
    external_post_id: str | None = None

    @classmethod
    def from_domain(cls, attempt: PublishAttempt) -> PublishAttemptDetail:
        return cls.model_validate(attempt.model_dump(include=set(cls.model_fields)))


class PublishStatusPublic(BaseModel):
    """Current (active) or latest publish job of an item — VIEWER variant (A-4 style)."""

    job_id: UUID
    item_id: UUID
    revision_id: UUID
    channel: str
    target_page_id: str
    status: str
    run_at: datetime
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime | None = None
    external_permalink: str | None = None
    created_at: datetime
    updated_at: datetime
    attempts: list[PublishAttemptPublic]

    @classmethod
    def from_domain(cls, job: PublishJob, attempts: list[PublishAttempt]) -> PublishStatusPublic:
        return cls(
            **_job_fields(job), attempts=[PublishAttemptPublic.from_domain(a) for a in attempts]
        )


class PublishStatusDetail(PublishStatusPublic):
    """OWNER/MANAGER variant: adds ``last_error_class``, ``external_post_id``, the lease and
    who authorised the job (D-055) plus detailed attempts."""

    last_error_class: str | None = None
    external_post_id: str | None = None
    leased_until: datetime | None = None
    lease_owner: str | None = None
    scheduled_by_tenant_id: UUID
    scheduled_by_user_id: UUID
    attempts: list[PublishAttemptDetail]  # type: ignore[assignment]

    @classmethod
    def from_domain(cls, job: PublishJob, attempts: list[PublishAttempt]) -> PublishStatusDetail:
        return cls(
            **_job_fields(job),
            last_error_class=job.last_error_class,
            external_post_id=job.external_post_id,
            leased_until=job.leased_until,
            lease_owner=job.lease_owner,
            scheduled_by_tenant_id=job.scheduled_by_tenant_id,
            scheduled_by_user_id=job.scheduled_by_user_id,
            attempts=[PublishAttemptDetail.from_domain(a) for a in attempts],
        )


def _job_fields(job: PublishJob) -> dict[str, Any]:
    return {
        "job_id": job.id,
        "item_id": job.item_id,
        "revision_id": job.revision_id,
        "channel": job.channel,
        "target_page_id": job.target_page_id,
        "status": job.status,
        "run_at": job.run_at,
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "next_attempt_at": job.next_attempt_at,
        "external_permalink": job.external_permalink,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }
