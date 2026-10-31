"""PostgreSQL implementation of :class:`PublishJobRepository` (SPRINT-07 B1/B6).

Lease discipline (B6-I3 exactly-once): the due ``queued`` jobs are selected with
``FOR UPDATE SKIP LOCKED`` so concurrent workers never see the same row, then flipped to
``leased`` with a **conditional** ``UPDATE … WHERE status = 'queued'``. Every other mutation is
also a conditional ``UPDATE`` whose ``rowcount`` decides the race.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.publishing.domain.models import (
    AttemptOutcome,
    ErrorClass,
    JobStatus,
    PublishAttempt,
    PublishJob,
)
from src.modules.publishing.exceptions import ActiveJobExistsError
from src.modules.publishing.infrastructure.orm import PublishAttemptRow, PublishJobRow

_ACTIVE = (JobStatus.QUEUED.value, JobStatus.LEASED.value)


class PostgresPublishJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------ jobs ---
    async def create_job(self, job: PublishJob) -> PublishJob:
        row = PublishJobRow(
            id=job.id,
            item_id=job.item_id,
            revision_id=job.revision_id,
            brand_id=job.brand_id,
            channel=job.channel,
            target_page_id=job.target_page_id,
            run_at=job.run_at,
            status=job.status.value,
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
            leased_until=job.leased_until,
            lease_owner=job.lease_owner,
            next_attempt_at=job.next_attempt_at,
            external_post_id=job.external_post_id,
            external_permalink=job.external_permalink,
            last_error_class=job.last_error_class.value if job.last_error_class else None,
            scheduled_by_tenant_id=job.scheduled_by_tenant_id,
            scheduled_by_user_id=job.scheduled_by_user_id,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            if "uq_publish_jobs_active_item" in str(exc.orig):
                # The caller's transaction is now failed; the request layer rolls it back,
                # so the status CAS issued earlier never commits (B2-I1 atomicity).
                raise ActiveJobExistsError() from exc
            raise
        return job

    async def get_job(self, job_id: UUID) -> PublishJob | None:
        row = await self._session.get(PublishJobRow, job_id)
        return _job_to_domain(row) if row is not None else None

    async def active_job_for_item(self, item_id: UUID) -> PublishJob | None:
        stmt = select(PublishJobRow).where(
            PublishJobRow.item_id == item_id, PublishJobRow.status.in_(_ACTIVE)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _job_to_domain(row) if row is not None else None

    async def latest_job_for_item(self, item_id: UUID) -> PublishJob | None:
        active = await self.active_job_for_item(item_id)
        if active is not None:
            return active
        stmt = (
            select(PublishJobRow)
            .where(PublishJobRow.item_id == item_id)
            .order_by(PublishJobRow.created_at.desc(), PublishJobRow.id.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _job_to_domain(row) if row is not None else None

    async def cancel_queued_job(self, item_id: UUID, *, now: datetime) -> JobStatus | None:
        active = await self.active_job_for_item(item_id)
        if active is None:
            return None
        stmt = (
            update(PublishJobRow)
            .where(PublishJobRow.id == active.id, PublishJobRow.status == JobStatus.QUEUED.value)
            .values(status=JobStatus.CANCELLED.value, next_attempt_at=None, updated_at=now)
        )
        result = await self._session.execute(stmt)
        return JobStatus.QUEUED if result.rowcount == 1 else JobStatus.LEASED

    async def lease_due_jobs(
        self, *, now: datetime, lease_until: datetime, owner: str, batch: int
    ) -> list[PublishJob]:
        pick = (
            select(PublishJobRow.id)
            .where(
                PublishJobRow.status == JobStatus.QUEUED.value,
                PublishJobRow.run_at <= now,
                or_(
                    PublishJobRow.next_attempt_at.is_(None),
                    PublishJobRow.next_attempt_at <= now,
                ),
            )
            .order_by(PublishJobRow.run_at, PublishJobRow.id)
            .limit(batch)
            .with_for_update(skip_locked=True)
        )
        ids = list((await self._session.execute(pick)).scalars().all())
        if not ids:
            return []
        await self._session.execute(
            update(PublishJobRow)
            .where(PublishJobRow.id.in_(ids), PublishJobRow.status == JobStatus.QUEUED.value)
            .values(
                status=JobStatus.LEASED.value,
                leased_until=lease_until,
                lease_owner=owner,
                attempt_count=PublishJobRow.attempt_count + 1,
                updated_at=now,
            )
        )
        rows = (
            (
                await self._session.execute(
                    select(PublishJobRow)
                    .where(PublishJobRow.id.in_(ids), PublishJobRow.lease_owner == owner)
                    .order_by(PublishJobRow.run_at, PublishJobRow.id)
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        return [_job_to_domain(r) for r in rows]

    async def expired_leases(self, *, now: datetime) -> list[PublishJob]:
        stmt = (
            select(PublishJobRow)
            .where(
                PublishJobRow.status == JobStatus.LEASED.value, PublishJobRow.leased_until < now
            )
            .order_by(PublishJobRow.leased_until, PublishJobRow.id)
            .execution_options(populate_existing=True)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_job_to_domain(r) for r in rows]

    async def requeue_expired(self, job_id: UUID, *, now: datetime) -> bool:
        stmt = (
            update(PublishJobRow)
            .where(
                PublishJobRow.id == job_id,
                PublishJobRow.status == JobStatus.LEASED.value,
                PublishJobRow.leased_until < now,
            )
            .values(
                status=JobStatus.QUEUED.value, leased_until=None, lease_owner=None, updated_at=now
            )
        )
        result = await self._session.execute(stmt)
        return result.rowcount == 1

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
        values: dict[str, object] = {
            "status": status.value,
            "leased_until": None,
            "lease_owner": None,
            "next_attempt_at": next_attempt_at,
            "updated_at": now,
        }
        if external_post_id is not None:
            values["external_post_id"] = external_post_id
        if external_permalink is not None:
            values["external_permalink"] = external_permalink
        if last_error_class is not None:
            values["last_error_class"] = last_error_class.value
        stmt = (
            update(PublishJobRow)
            .where(PublishJobRow.id == job_id, PublishJobRow.status == JobStatus.LEASED.value)
            .values(**values)
        )
        result = await self._session.execute(stmt)
        return result.rowcount == 1

    # -------------------------------------------------------- attempts ---
    async def start_attempt(self, attempt: PublishAttempt) -> PublishAttempt:
        row = PublishAttemptRow(
            id=attempt.id,
            job_id=attempt.job_id,
            attempt_no=attempt.attempt_no,
            started_at=attempt.started_at,
            finished_at=attempt.finished_at,
            outcome=attempt.outcome.value if attempt.outcome else None,
            error_class=attempt.error_class.value if attempt.error_class else None,
            graph_error_code=attempt.graph_error_code,
            graph_error_subcode=attempt.graph_error_subcode,
            graph_trace_id=attempt.graph_trace_id,
            external_post_id=attempt.external_post_id,
        )
        self._session.add(row)
        await self._session.flush()
        return attempt

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
        stmt = (
            update(PublishAttemptRow)
            .where(PublishAttemptRow.id == attempt_id, PublishAttemptRow.outcome.is_(None))
            .values(
                outcome=outcome.value,
                finished_at=finished_at,
                error_class=error_class.value if error_class else None,
                graph_error_code=graph_error_code,
                graph_error_subcode=graph_error_subcode,
                graph_trace_id=graph_trace_id,
                external_post_id=external_post_id,
            )
        )
        result = await self._session.execute(stmt)
        return result.rowcount == 1

    async def list_attempts(self, job_id: UUID) -> list[PublishAttempt]:
        stmt = (
            select(PublishAttemptRow)
            .where(PublishAttemptRow.job_id == job_id)
            .order_by(PublishAttemptRow.attempt_no)
            .execution_options(populate_existing=True)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_attempt_to_domain(r) for r in rows]

    async def open_attempt(self, job_id: UUID) -> PublishAttempt | None:
        stmt = (
            select(PublishAttemptRow)
            .where(PublishAttemptRow.job_id == job_id, PublishAttemptRow.outcome.is_(None))
            .order_by(PublishAttemptRow.attempt_no.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _attempt_to_domain(row) if row is not None else None

    async def last_creation_id(self, job_id: UUID) -> str | None:
        stmt = (
            select(PublishAttemptRow.external_post_id)
            .where(
                PublishAttemptRow.job_id == job_id,
                PublishAttemptRow.outcome.is_not(None),
                PublishAttemptRow.outcome != AttemptOutcome.SUCCEEDED.value,
                PublishAttemptRow.external_post_id.is_not(None),
            )
            .order_by(PublishAttemptRow.attempt_no.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()


def _job_to_domain(row: PublishJobRow) -> PublishJob:
    return PublishJob(
        id=row.id,
        item_id=row.item_id,
        revision_id=row.revision_id,
        brand_id=row.brand_id,
        channel=row.channel,
        target_page_id=row.target_page_id,
        run_at=row.run_at,
        status=JobStatus(row.status),
        attempt_count=row.attempt_count,
        max_attempts=row.max_attempts,
        leased_until=row.leased_until,
        lease_owner=row.lease_owner,
        next_attempt_at=row.next_attempt_at,
        external_post_id=row.external_post_id,
        external_permalink=row.external_permalink,
        last_error_class=ErrorClass(row.last_error_class) if row.last_error_class else None,
        scheduled_by_tenant_id=row.scheduled_by_tenant_id,
        scheduled_by_user_id=row.scheduled_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _attempt_to_domain(row: PublishAttemptRow) -> PublishAttempt:
    return PublishAttempt(
        id=row.id,
        job_id=row.job_id,
        attempt_no=row.attempt_no,
        started_at=row.started_at,
        finished_at=row.finished_at,
        outcome=AttemptOutcome(row.outcome) if row.outcome else None,
        error_class=ErrorClass(row.error_class) if row.error_class else None,
        graph_error_code=row.graph_error_code,
        graph_error_subcode=row.graph_error_subcode,
        graph_trace_id=row.graph_trace_id,
        external_post_id=row.external_post_id,
    )
