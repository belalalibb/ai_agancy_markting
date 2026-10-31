"""``PostgresScheduler`` — the publish worker (SPRINT-07 B6).

One ``run_once(now)`` cycle, each step in its **own committed transaction** so a crash between
steps never leaves an attempt without a row:

1. **Recovery** — ``leased`` jobs whose lease expired: an *open* attempt (``outcome IS NULL``)
   means the previous worker died mid-Graph-call ⇒ the outcome is **ambiguous** (the post may
   be live): attempt ``ambiguous`` + job ``failed(ambiguous_outcome)`` + item
   ``scheduled → publish_failed`` + event ``publish_failed``. No open attempt ⇒ requeue.
2. **Lease** — ``SELECT … FOR UPDATE SKIP LOCKED`` batch ⇒ ``leased`` by ``<hostname:pid>``,
   ``attempt_count += 1`` (two workers never lease the same job — B6-I3).
3. **Per job** — insert the attempt (``outcome NULL``) and commit ⇒ call the ``Publisher``
   (no DB session held) ⇒ result transaction: ``succeeded`` ⇒ job ``succeeded`` + item CAS
   ``scheduled → published`` (+``published_at``) + event ``published``; retryable and
   ``attempt_count < max_attempts`` ⇒ job back to ``queued`` with ``next_attempt_at = now +
   backoff[attempt_count - 1]``; otherwise job ``failed`` + CAS ``→ publish_failed`` + event.
   ``token_invalid`` ⇒ the connection is marked ``reauth_required`` through the
   ``StatusMarker`` **outside** the result transaction (B6-I5).

Logs carry ids and classes only — never post text or tokens.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.config import Settings
from src.core.metrics import counters
from src.core.security import TokenEncryptionService
from src.modules.content.domain.models import (
    ApprovalAction,
    ApprovalEvent,
    Channel,
    ContentStatus,
)
from src.modules.content.domain.repository import ContentRepository
from src.modules.content.infrastructure.postgres_repository import PostgresContentRepository
from src.modules.publishing.domain.models import (
    AttemptOutcome,
    ErrorClass,
    JobStatus,
    PublishAttempt,
    PublishJob,
    PublishOutcome,
)
from src.modules.publishing.domain.publisher import (
    PageTokenProvider,
    Publisher,
    ResolvedPageTarget,
)
from src.modules.publishing.domain.repository import PublishJobRepository
from src.modules.publishing.infrastructure.meta_publisher import PostgresPageTokenProvider
from src.modules.publishing.infrastructure.postgres_repository import PostgresPublishJobRepository
from src.modules.social_connect.domain.models import AccountStatus
from src.modules.social_connect.domain.repository import ConnectedAccountRepository
from src.modules.social_connect.domain.status_marker import StatusMarker
from src.modules.social_connect.infrastructure.postgres_repository import (
    PostgresConnectedAccountRepository,
)

logger = logging.getLogger(__name__)

SessionFactory = async_sessionmaker[AsyncSession]
SessionFactoryProvider = Callable[[], SessionFactory]
JobsRepoFactory = Callable[[AsyncSession], PublishJobRepository]
ContentRepoFactory = Callable[[AsyncSession], ContentRepository]
ConnectionsRepoFactory = Callable[[AsyncSession], ConnectedAccountRepository]


# SPRINT-08 A-1: shared message of the two-layer guard (``main._start_scheduler`` and
# ``PostgresScheduler.__init__``) — a worker that cannot decrypt page tokens must not boot.
SCHEDULER_REQUIRES_ENCRYPTION_KEY = "SCHEDULER_ENABLED requires TOKEN_ENCRYPTION_KEY"
# SPRINT-08 B-1: the scheduler no longer owns one publisher — it resolves one per job channel
# (Meta ⇒ MetaPublisher, structural ⇒ StructuralPublisher) through the channel registry.
PublisherResolver = Callable[[Channel], Publisher]


def default_lease_owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"[:64]


class Scheduler(Protocol):
    async def run_once(self, now: datetime) -> int:
        """Process every due job once; returns the number of jobs *processed* (attempted)."""
        ...


class PostgresScheduler:
    def __init__(
        self,
        session_factory: SessionFactoryProvider,
        publisher_resolver: PublisherResolver,
        status_marker: StatusMarker,
        settings: Settings,
        *,
        encryption: TokenEncryptionService | None = None,
        lease_owner: str | None = None,
        jobs_repo: JobsRepoFactory = PostgresPublishJobRepository,
        content_repo: ContentRepoFactory = PostgresContentRepository,
        connections_repo: ConnectionsRepoFactory = PostgresConnectedAccountRepository,
    ) -> None:
        """The three ``*_repo`` factories default to the PostgreSQL repositories; tests inject
        the in-memory fakes so the cycle logic is unit-testable without a database.

        ``encryption`` is mandatory (A-1): ``None`` raises ``RuntimeError`` with
        ``SCHEDULER_REQUIRES_ENCRYPTION_KEY`` — second layer behind ``main._start_scheduler``.

        ``publisher_resolver`` (SPRINT-08 B-1) maps ``job.channel`` to the ``Publisher`` that
        handles it; ``main`` builds it from the channel registry, tests pass a lambda.
        """
        if encryption is None:
            raise RuntimeError(SCHEDULER_REQUIRES_ENCRYPTION_KEY)
        self._session_factory = session_factory
        self._resolve_publisher = publisher_resolver
        self._status_marker = status_marker
        self._settings = settings
        self._encryption = encryption
        self._owner = lease_owner or default_lease_owner()
        self._jobs_repo = jobs_repo
        self._content_repo = content_repo
        self._connections_repo = connections_repo

    # ------------------------------------------------------------------ cycle ---
    async def run_once(self, now: datetime) -> int:
        await self._recover_expired_leases(now)
        jobs = await self._lease_batch(now)
        for job in jobs:
            await self._process(job, now)
        return len(jobs)

    # ------------------------------------------------------------- step 1 ---
    async def _recover_expired_leases(self, now: datetime) -> None:
        async with self._session_factory()() as session, session.begin():
            jobs_repo = self._jobs_repo(session)
            content_repo = self._content_repo(session)
            for job in await jobs_repo.expired_leases(now=now):
                attempt = await jobs_repo.open_attempt(job.id)
                if attempt is None:
                    await jobs_repo.requeue_expired(job.id, now=now)
                    logger.info("publish: job=%s requeued after expired lease", job.id)
                    continue
                await jobs_repo.finish_attempt(
                    attempt.id,
                    outcome=AttemptOutcome.AMBIGUOUS,
                    finished_at=now,
                    error_class=ErrorClass.AMBIGUOUS_OUTCOME,
                )
                await jobs_repo.complete_job(
                    job.id,
                    status=JobStatus.FAILED,
                    now=now,
                    last_error_class=ErrorClass.AMBIGUOUS_OUTCOME,
                )
                await self._fail_item(content_repo, job, now)
                counters.increment("publish_failed")
                self._log(job, "ambiguous", attempt.attempt_no)

    # ------------------------------------------------------------- step 2 ---
    async def _lease_batch(self, now: datetime) -> list[PublishJob]:
        lease_until = now + timedelta(seconds=self._settings.scheduler_lease_seconds)
        async with self._session_factory()() as session, session.begin():
            return await self._jobs_repo(session).lease_due_jobs(
                now=now,
                lease_until=lease_until,
                owner=self._owner,
                batch=self._settings.scheduler_batch_size,
            )

    # ------------------------------------------------------------- step 3 ---
    async def _process(self, job: PublishJob, now: datetime) -> None:
        # 3a — attempt row + revision + IG creation id, committed before any Graph call.
        async with self._session_factory()() as session, session.begin():
            jobs_repo = self._jobs_repo(session)
            attempt = await jobs_repo.start_attempt(
                PublishAttempt(job_id=job.id, attempt_no=job.attempt_count, started_at=now)
            )
            revision = await self._content_repo(session).get_revision(job.revision_id)
            last_creation_id = await jobs_repo.last_creation_id(job.id)

        # 3b — provider call; the token provider opens its own short-lived read session.
        if revision is None:
            outcome = PublishOutcome.failure(ErrorClass.INVALID_REQUEST)
        else:
            outcome = await self._resolve_publisher(Channel(job.channel)).publish(
                job,
                revision,
                self._page_token_provider(),
                last_creation_id=last_creation_id,
            )

        # 3c — result transaction.
        finished_at = datetime.now(tz=UTC)
        async with self._session_factory()() as session, session.begin():
            jobs_repo = self._jobs_repo(session)
            content_repo = self._content_repo(session)
            await jobs_repo.finish_attempt(
                attempt.id,
                outcome=outcome.kind,
                finished_at=finished_at,
                error_class=outcome.error_class,
                graph_error_code=outcome.graph_error_code,
                graph_error_subcode=outcome.graph_error_subcode,
                graph_trace_id=outcome.graph_trace_id,
                external_post_id=outcome.external_post_id,
            )
            if outcome.kind is AttemptOutcome.SUCCEEDED:
                await jobs_repo.complete_job(
                    job.id,
                    status=JobStatus.SUCCEEDED,
                    now=finished_at,
                    external_post_id=outcome.external_post_id,
                    external_permalink=outcome.permalink,
                )
                await content_repo.compare_and_set_status(
                    job.item_id,
                    expected=ContentStatus.SCHEDULED,
                    new=ContentStatus.PUBLISHED,
                    scheduled_for=None,
                    updated_at=finished_at,
                    published_at=finished_at,
                )
                await content_repo.add_event(
                    ApprovalEvent(
                        item_id=job.item_id,
                        revision_id=job.revision_id,
                        action=ApprovalAction.PUBLISHED,
                        actor_tenant_id=None,
                        actor_user_id=None,
                        created_at=finished_at,
                    )
                )
                counters.increment("publish_succeeded")
                self._log(job, "succeeded", attempt.attempt_no)
            elif (
                outcome.kind is AttemptOutcome.RETRYABLE_FAILURE
                and job.attempt_count < job.max_attempts
            ):
                await jobs_repo.complete_job(
                    job.id,
                    status=JobStatus.QUEUED,
                    now=finished_at,
                    last_error_class=outcome.error_class,
                    # Directive B6: ``now + backoff[attempt_count - 1]`` on the *cycle* clock.
                    next_attempt_at=now + timedelta(seconds=self._backoff(job)),
                )
                counters.increment("publish_retried")
                self._log(job, f"retry:{outcome.error_class}", attempt.attempt_no)
            else:
                await jobs_repo.complete_job(
                    job.id,
                    status=JobStatus.FAILED,
                    now=finished_at,
                    last_error_class=outcome.error_class,
                )
                await self._fail_item(content_repo, job, finished_at)
                counters.increment("publish_failed")
                self._log(job, f"failed:{outcome.error_class}", attempt.attempt_no)

        # 3d — B6-I5: out-of-band, after the result transaction committed (lesson 7).
        if outcome.connection_id_to_mark is not None:
            await self._status_marker.mark_out_of_band(
                job.brand_id, outcome.connection_id_to_mark, AccountStatus.REAUTH_REQUIRED
            )

    # ---------------------------------------------------------------- helpers ---
    def _page_token_provider(self) -> PageTokenProvider:
        return _SessionPerCallTokenProvider(
            self._session_factory, self._encryption, self._connections_repo
        )

    def _backoff(self, job: PublishJob) -> int:
        schedule = self._settings.publish_retry_backoff_seconds
        index = min(max(job.attempt_count - 1, 0), len(schedule) - 1)
        return schedule[index]

    @staticmethod
    async def _fail_item(content_repo: ContentRepository, job: PublishJob, now: datetime) -> None:
        await content_repo.compare_and_set_status(
            job.item_id,
            expected=ContentStatus.SCHEDULED,
            new=ContentStatus.PUBLISH_FAILED,
            scheduled_for=None,
            updated_at=now,
        )
        await content_repo.add_event(
            ApprovalEvent(
                item_id=job.item_id,
                revision_id=job.revision_id,
                action=ApprovalAction.PUBLISH_FAILED,
                actor_tenant_id=None,
                actor_user_id=None,
                created_at=now,
            )
        )

    @staticmethod
    def _log(job: PublishJob, outcome: str, attempt_no: int) -> None:
        logger.info(
            "publish: job=%s item=%s brand=%s channel=%s outcome=%s attempt=%s",
            job.id,
            job.item_id,
            job.brand_id,
            job.channel,
            outcome,
            attempt_no,
        )


class _SessionPerCallTokenProvider:
    """``PageTokenProvider`` that opens one short read transaction per ``resolve``."""

    def __init__(
        self,
        session_factory: SessionFactoryProvider,
        encryption: TokenEncryptionService | None,
        connections_repo: ConnectionsRepoFactory = PostgresConnectedAccountRepository,
    ) -> None:
        self._session_factory = session_factory
        self._encryption = encryption
        self._connections_repo = connections_repo

    async def resolve(self, brand_id: UUID, page_id: str) -> ResolvedPageTarget | None:
        if self._encryption is None:
            return None
        async with self._session_factory()() as session:
            provider = PostgresPageTokenProvider(self._connections_repo(session), self._encryption)
            return await provider.resolve(brand_id, page_id)


# ----------------------------------------------------------------------- loop ---
async def scheduler_loop(scheduler: Scheduler, poll_seconds: float) -> None:
    """Run ``run_once`` forever; a failing cycle is logged (ERROR) and counted, never fatal."""
    while True:
        try:
            await scheduler.run_once(datetime.now(tz=UTC))
        except asyncio.CancelledError:
            raise
        except Exception:
            counters.increment("scheduler_cycle_errors")
            logger.exception("scheduler cycle failed")
        await asyncio.sleep(poll_seconds)


__all__ = [
    "PostgresScheduler",
    "Scheduler",
    "default_lease_owner",
    "scheduler_loop",
]
