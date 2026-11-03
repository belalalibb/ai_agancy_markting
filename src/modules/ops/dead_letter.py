"""A-3 dead-letter — ``publish_jobs.status = 'failed'`` made visible + admin requeue.

Cross-module reads/writes are SQL text over ``publish_jobs`` / ``content_items`` /
``approval_events`` (D-016 — no other module's ORM is imported). The requeue is a CAS:
``UPDATE publish_jobs … WHERE id = :id AND status = 'failed'``; zero rows ⇒ ``job_not_failed``.
The item goes ``publish_failed → scheduled`` (CAS, D-055 event ``requeued_by_admin``). The
scheduler's lease/recover logic (B6-I3) is untouched — the job simply becomes ``queued`` with
``attempt_count = 0`` and ``next_attempt_at = now``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MAX_DEAD_LETTER = 200


class DeadLetterJob(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: uuid.UUID
    item_id: uuid.UUID
    brand_id: uuid.UUID
    channel: str
    error_class: str | None
    attempt_count: int
    max_attempts: int
    failed_at: datetime


class DeadLetterRepository(Protocol):
    async def list_failed(self, *, limit: int) -> list[DeadLetterJob]: ...

    async def requeue(self, job_id: uuid.UUID, *, now: datetime) -> DeadLetterJob | None:
        """CAS ``failed ⇒ queued``; ``None`` when the job is not ``failed`` (or unknown)."""
        ...

    async def reschedule_item(
        self, item_id: uuid.UUID, revision_id: uuid.UUID, *, now: datetime
    ) -> bool:
        """CAS ``publish_failed ⇒ scheduled`` + event ``requeued_by_admin`` (same tx)."""
        ...


_LIST = text(
    "SELECT id, item_id, brand_id, channel, last_error_class, attempt_count, max_attempts, "
    "updated_at FROM publish_jobs WHERE status = 'failed' ORDER BY updated_at DESC LIMIT :limit"
)
_REQUEUE = text(
    "UPDATE publish_jobs SET status = 'queued', attempt_count = 0, next_attempt_at = :now, "
    "leased_until = NULL, lease_owner = NULL, updated_at = :now "
    "WHERE id = :job_id AND status = 'failed' "
    "RETURNING id, item_id, brand_id, channel, last_error_class, attempt_count, max_attempts, "
    "updated_at, revision_id, run_at"
)
_RESCHEDULE_ITEM = text(
    "UPDATE content_items SET status = 'scheduled', scheduled_for = :now, updated_at = :now "
    "WHERE id = :item_id AND status = 'publish_failed' RETURNING id"
)
_EVENT = text(
    "INSERT INTO approval_events (id, item_id, revision_id, action, actor_tenant_id, "
    "actor_user_id, note, created_at) VALUES (:id, :item_id, :revision_id, "
    "'requeued_by_admin', NULL, NULL, NULL, :now)"
)


def _job(row: tuple) -> DeadLetterJob:  # type: ignore[type-arg]
    return DeadLetterJob(
        job_id=row[0],
        item_id=row[1],
        brand_id=row[2],
        channel=row[3],
        error_class=row[4],
        attempt_count=row[5],
        max_attempts=row[6],
        failed_at=row[7],
    )


class PgDeadLetterRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self.last_revision_id: uuid.UUID | None = None

    async def list_failed(self, *, limit: int) -> list[DeadLetterJob]:
        rows = await self._session.execute(_LIST, {"limit": min(max(limit, 1), MAX_DEAD_LETTER)})
        return [_job(tuple(r)) for r in rows.all()]

    async def requeue(self, job_id: uuid.UUID, *, now: datetime) -> DeadLetterJob | None:
        row = (await self._session.execute(_REQUEUE, {"job_id": job_id, "now": now})).first()
        if row is None:
            return None
        self.last_revision_id = row[8]
        return _job(tuple(row))

    async def reschedule_item(
        self, item_id: uuid.UUID, revision_id: uuid.UUID, *, now: datetime
    ) -> bool:
        row = (
            await self._session.execute(_RESCHEDULE_ITEM, {"item_id": item_id, "now": now})
        ).first()
        if row is None:
            return False
        await self._session.execute(
            _EVENT,
            {"id": uuid.uuid4(), "item_id": item_id, "revision_id": revision_id, "now": now},
        )
        return True


__all__ = ["MAX_DEAD_LETTER", "DeadLetterJob", "DeadLetterRepository", "PgDeadLetterRepository"]
