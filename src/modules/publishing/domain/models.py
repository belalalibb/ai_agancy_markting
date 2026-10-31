"""Domain models of ``publishing`` (SPRINT-07 B1/B5)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Final
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class JobStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


ACTIVE_JOB_STATUSES: Final[frozenset[JobStatus]] = frozenset({JobStatus.QUEUED, JobStatus.LEASED})


class AttemptOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    RETRYABLE_FAILURE = "retryable_failure"
    PERMANENT_FAILURE = "permanent_failure"
    AMBIGUOUS = "ambiguous"


class ErrorClass(StrEnum):
    """Internal classification of a failed publish (never Meta's message text — B4)."""

    TOKEN_INVALID = "token_invalid"  # noqa: S105 - classification label, not a credential
    PERMISSION_DENIED = "permission_denied"
    RATE_LIMITED = "rate_limited"
    TRANSIENT = "transient"
    INVALID_REQUEST = "invalid_request"
    CONNECTION_UNAVAILABLE = "connection_unavailable"
    AMBIGUOUS_OUTCOME = "ambiguous_outcome"
    UNKNOWN = "unknown"
    # SPRINT-08 B-1: the channel is in the registry but not activated (structural adapter).
    # **Final** — never retried (not in RETRYABLE_ERROR_CLASSES).
    CHANNEL_NOT_ACTIVATED = "channel_not_activated"


RETRYABLE_ERROR_CLASSES: Final[frozenset[ErrorClass]] = frozenset(
    {ErrorClass.RATE_LIMITED, ErrorClass.TRANSIENT}
)


class PublishJob(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    item_id: UUID
    revision_id: UUID
    brand_id: UUID
    channel: str = Field(max_length=16)
    target_page_id: str = Field(max_length=64)
    run_at: datetime
    status: JobStatus = JobStatus.QUEUED
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1)
    leased_until: datetime | None = None
    lease_owner: str | None = Field(default=None, max_length=64)
    next_attempt_at: datetime | None = None
    external_post_id: str | None = Field(default=None, max_length=128)
    external_permalink: str | None = None
    last_error_class: ErrorClass | None = None
    # D-055: who authorised the external action (schedule / retry).
    scheduled_by_tenant_id: UUID
    scheduled_by_user_id: UUID
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class PublishAttempt(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    job_id: UUID
    attempt_no: int = Field(ge=1)
    started_at: datetime = Field(default_factory=_utcnow)
    finished_at: datetime | None = None
    outcome: AttemptOutcome | None = None
    error_class: ErrorClass | None = None
    graph_error_code: int | None = None
    graph_error_subcode: int | None = None
    graph_trace_id: str | None = Field(default=None, max_length=64)
    external_post_id: str | None = Field(default=None, max_length=128)


class PublishOutcome(BaseModel):
    """What a :class:`Publisher` returns — never raises for provider failures."""

    model_config = ConfigDict(frozen=True)

    kind: AttemptOutcome
    error_class: ErrorClass | None = None
    graph_error_code: int | None = None
    graph_error_subcode: int | None = None
    graph_trace_id: str | None = None
    external_post_id: str | None = None  # post id, or the IG ``creation_id`` after step 1
    permalink: str | None = None
    # B6-I5: the publisher saw an invalid Page token ⇒ the connection must be marked
    # ``reauth_required`` by the scheduler *outside* the result transaction (lesson 7).
    connection_id_to_mark: UUID | None = None

    @classmethod
    def success(cls, *, external_post_id: str, permalink: str | None) -> PublishOutcome:
        return cls(
            kind=AttemptOutcome.SUCCEEDED, external_post_id=external_post_id, permalink=permalink
        )

    @classmethod
    def failure(
        cls,
        error_class: ErrorClass,
        *,
        graph_error_code: int | None = None,
        graph_error_subcode: int | None = None,
        graph_trace_id: str | None = None,
        external_post_id: str | None = None,
        connection_id_to_mark: UUID | None = None,
    ) -> PublishOutcome:
        kind = (
            AttemptOutcome.RETRYABLE_FAILURE
            if error_class in RETRYABLE_ERROR_CLASSES
            else AttemptOutcome.PERMANENT_FAILURE
        )
        return cls(
            kind=kind,
            error_class=error_class,
            graph_error_code=graph_error_code,
            graph_error_subcode=graph_error_subcode,
            graph_trace_id=graph_trace_id,
            external_post_id=external_post_id,
            connection_id_to_mark=connection_id_to_mark,
        )

    @classmethod
    def ambiguous(
        cls,
        *,
        graph_error_code: int | None = None,
        graph_error_subcode: int | None = None,
        graph_trace_id: str | None = None,
        external_post_id: str | None = None,
    ) -> PublishOutcome:
        """SPRINT-09 A-2 / D-070: the side-effecting Graph POST was sent once and its outcome
        is unknown (the post may be live). Recorded as ``ambiguous`` — never retried."""
        return cls(
            kind=AttemptOutcome.AMBIGUOUS,
            error_class=ErrorClass.AMBIGUOUS_OUTCOME,
            graph_error_code=graph_error_code,
            graph_error_subcode=graph_error_subcode,
            graph_trace_id=graph_trace_id,
            external_post_id=external_post_id,
        )
