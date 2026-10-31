"""Persistence contract of ``publishing`` (D-013: Protocol here, PG impl in ``infrastructure``,
fakes only in ``tests``).

Every mutation is a **conditional** ``UPDATE`` whose ``rowcount`` decides the race (same
discipline as ``content.compare_and_set_status``). The lease is taken with
``FOR UPDATE SKIP LOCKED`` so two workers never pick the same job (B6-I3).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from src.modules.publishing.domain.models import (
    AttemptOutcome,
    ErrorClass,
    JobStatus,
    PublishAttempt,
    PublishJob,
)


@runtime_checkable
class PublishJobRepository(Protocol):
    # ------------------------------------------------------------ jobs ---
    async def create_job(self, job: PublishJob) -> PublishJob:
        """Insert a ``queued`` job. Raises :class:`ActiveJobExistsError` when the item
        already has an active (``queued``/``leased``) job — ``uq_publish_jobs_active_item``."""
        ...

    async def get_job(self, job_id: UUID) -> PublishJob | None: ...

    async def latest_job_for_item(self, item_id: UUID) -> PublishJob | None:
        """The current active job if any, else the most recently created one."""
        ...

    async def active_job_for_item(self, item_id: UUID) -> PublishJob | None:
        """The ``queued``/``leased`` job of the item (at most one by the partial unique index)."""
        ...

    async def cancel_queued_job(self, item_id: UUID, *, now: datetime) -> JobStatus | None:
        """``queued → cancelled`` for the item's active job. Returns the status the active job
        had **before** the call (``queued`` ⇒ cancelled; ``leased`` ⇒ untouched, caller
        answers 409 ``publish_in_progress``) or ``None`` when no active job exists."""
        ...

    async def lease_due_jobs(
        self, *, now: datetime, lease_until: datetime, owner: str, batch: int
    ) -> list[PublishJob]:
        """``SELECT … WHERE status='queued' AND run_at <= now AND (next_attempt_at IS NULL OR
        next_attempt_at <= now) ORDER BY run_at LIMIT batch FOR UPDATE SKIP LOCKED`` then
        ``status='leased', leased_until, lease_owner, attempt_count += 1``. Returns the
        leased jobs (already incremented)."""
        ...

    async def expired_leases(self, *, now: datetime) -> list[PublishJob]:
        """``leased`` jobs whose ``leased_until < now`` (recovery step, B6)."""
        ...

    async def requeue_expired(self, job_id: UUID, *, now: datetime) -> bool:
        """``leased → queued`` (lease cleared, ``attempt_count`` **not** decremented — the lost
        attempt counts) iff the lease is still expired. ``True`` iff one row changed."""
        ...

    async def complete_job(
        self,
        job_id: UUID,
        *,
        status: JobStatus,
        now: datetime,
        external_post_id: str | None = None,
        external_permalink: str | None = None,
        last_error_class: ErrorClass | None = None,
        next_attempt_at: datetime | None = None,
    ) -> bool:
        """``leased → status`` (``succeeded`` / ``failed`` / back to ``queued`` with
        ``next_attempt_at`` for a retry). Clears the lease. ``True`` iff one row changed."""
        ...

    # -------------------------------------------------------- attempts ---
    async def start_attempt(self, attempt: PublishAttempt) -> PublishAttempt:
        """Insert the attempt row with ``outcome IS NULL`` **before** the Graph call."""
        ...

    async def finish_attempt(
        self,
        attempt_id: UUID,
        *,
        outcome: AttemptOutcome,
        finished_at: datetime,
        error_class: ErrorClass | None = None,
        graph_error_code: int | None = None,
        graph_error_subcode: int | None = None,
        graph_trace_id: str | None = None,
        external_post_id: str | None = None,
    ) -> bool:
        """Fill the outcome iff it is still ``NULL`` (idempotent). ``True`` iff one row
        changed."""
        ...

    async def list_attempts(self, job_id: UUID) -> list[PublishAttempt]:
        """Oldest first."""
        ...

    async def open_attempt(self, job_id: UUID) -> PublishAttempt | None:
        """The attempt with ``outcome IS NULL`` for this job, if any."""
        ...

    async def last_creation_id(self, job_id: UUID) -> str | None:
        """IG two-step (B4): the ``external_post_id`` (container ``creation_id``) left by the
        most recent *failed* attempt, so a retry only re-runs ``media_publish``."""
        ...
