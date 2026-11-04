"""Ops service (SPRINT-13 Part A): audited admin acts over one request session.

Every act writes exactly one ``admin_actions`` row **in the same transaction** (A-1): the
request session commits both or neither. ``PlatformFlags`` is the 30-second in-process cache
in front of ``platform_flags`` (A-2) — DB first, ``Settings`` as the fallback so nothing
changes until an admin flips a key.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from src.core.config import Settings
from src.modules.ops.domain import (
    FLAG_CACHE_SECONDS,
    FLAG_KEYS,
    REQUEUE_GRACE_ATTEMPTS,
    AdminAction,
    AdminBrandView,
    AdminWaSummary,
    BrandSuspension,
    DeadLetterJob,
    PlatformFlag,
)
from src.modules.ops.exceptions import (
    BrandNotFoundError,
    FlagUnknownError,
    JobNotFailedError,
    JobNotFoundError,
    ManualVerificationRequiredError,
    PublishInProgressError,
)

AMBIGUOUS_OUTCOME = "ambiguous_outcome"  # publishing ErrorClass value (D-070)

logger = logging.getLogger(__name__)

FlagsLoader = Callable[[], Awaitable[dict[str, PlatformFlag]]]


def _now() -> datetime:
    return datetime.now(tz=UTC)


class PlatformFlags:
    """Process-wide flag cache (A-2): ``get(key)`` ⇒ DB value if a row exists, else the
    ``Settings`` attribute of the same name (``platform_readonly`` defaults to ``False``)."""

    def __init__(self, *, ttl_seconds: int = FLAG_CACHE_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._loaded_at: float | None = None
        self._rows: dict[str, PlatformFlag] = {}

    def invalidate(self) -> None:
        self._loaded_at = None
        self._rows = {}

    def mark_loaded(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        """After a failed read: keep the previous rows for one TTL (no DB hammering)."""
        self._loaded_at = clock()

    async def refresh(
        self, loader: FlagsLoader, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        now = clock()
        if self._loaded_at is not None and now - self._loaded_at < self._ttl:
            return
        self._rows = await loader()
        self._loaded_at = now

    def resolve(self, key: str, settings: Settings) -> PlatformFlag:
        if key not in FLAG_KEYS:
            raise FlagUnknownError(f"unknown flag {key!r}")
        row = self._rows.get(key)
        if row is not None:
            return row
        value = bool(getattr(settings, key, False))
        return PlatformFlag(key=key, value=value, source="settings")  # type: ignore[arg-type]


PLATFORM_FLAGS = PlatformFlags()


class OpsService:
    def __init__(self, repo: Any, *, flags: PlatformFlags = PLATFORM_FLAGS) -> None:
        self._repo = repo
        self._flags = flags

    # --- A-1 audit -------------------------------------------------------------------------
    async def record(
        self,
        *,
        actor_user_id: UUID | None,
        action: str,
        target_kind: str,
        target_id: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> AdminAction:
        row = AdminAction(
            actor_user_id=actor_user_id,
            action=action,
            target_kind=target_kind,
            target_id=target_id,
            before=before or {},
            after=after or {},
            at=now or _now(),
        )
        return await self._repo.add_action(row)

    async def list_actions(
        self, *, target_kind: str | None, target_id: str | None, limit: int
    ) -> list[AdminAction]:
        return await self._repo.list_actions(
            target_kind=target_kind, target_id=target_id, limit=limit
        )

    # --- A-2 flags -------------------------------------------------------------------------
    async def flags(self, settings: Settings) -> list[PlatformFlag]:
        await self._flags.refresh(self._repo.get_flags)
        return [self._flags.resolve(key, settings) for key in FLAG_KEYS]

    async def set_flag(
        self, key: str, value: bool, *, actor_user_id: UUID, settings: Settings
    ) -> PlatformFlag:
        if key not in FLAG_KEYS:
            raise FlagUnknownError(f"unknown flag {key!r}")
        await self._flags.refresh(self._repo.get_flags)
        before = self._flags.resolve(key, settings)
        now = _now()
        stored = await self._repo.set_flag(key, value, now=now)
        await self.record(
            actor_user_id=actor_user_id,
            action="flag_set",
            target_kind="platform_flag",
            target_id=key,
            before={"value": before.value, "source": before.source},
            after={"value": value},
            now=now,
        )
        self._flags.invalidate()
        return stored

    # --- A-2 suspension --------------------------------------------------------------------
    async def set_suspended(
        self, brand_id: UUID, *, suspended: bool, actor_user_id: UUID
    ) -> BrandSuspension:
        current = await self._repo.brand_suspension(brand_id)
        if current is None:
            raise BrandNotFoundError()
        now = _now()
        at = now if suspended else None
        await self._repo.set_suspended(brand_id, at)
        await self.record(
            actor_user_id=actor_user_id,
            action="brand_suspended" if suspended else "brand_unsuspended",
            target_kind="brand",
            target_id=str(brand_id),
            before={"suspended": current.suspended_at is not None},
            after={"suspended": suspended},
            now=now,
        )
        return BrandSuspension(brand_id=brand_id, suspended_at=at)

    # --- A-3 dead letter -------------------------------------------------------------------
    async def dead_letter(self, *, limit: int) -> list[DeadLetterJob]:
        return await self._repo.dead_letter(limit=limit)

    async def requeue(self, job_id: UUID, *, actor_user_id: UUID) -> DeadLetterJob:
        found = await self._repo.job_status(job_id)
        if found is None:
            raise JobNotFoundError()
        job, status, revision_id = found
        if status != "failed":
            raise JobNotFailedError(f"job is {status!r}, only failed jobs can be requeued")
        if job.error_class == AMBIGUOUS_OUTCOME:  # F-1 / D-070: verify by hand, never requeue
            raise ManualVerificationRequiredError()
        if await self._repo.item_has_active_job(job.item_id):  # F-2
            raise PublishInProgressError()
        now = _now()
        if not await self._repo.requeue(
            job,
            revision_id=revision_id,
            actor_user_id=actor_user_id,
            now=now,
            grace=REQUEUE_GRACE_ATTEMPTS,
        ):
            raise JobNotFailedError("job changed state concurrently")
        new_max = job.attempt_count + REQUEUE_GRACE_ATTEMPTS
        await self.record(
            actor_user_id=actor_user_id,
            action="job_requeued",
            target_kind="publish_job",
            target_id=str(job_id),
            before={
                "status": "failed",
                "attempt_count": job.attempt_count,
                "max_attempts": job.max_attempts,
            },
            after={
                "status": "queued",
                "attempt_count": job.attempt_count,
                "max_attempts": new_max,
                "item_id": str(job.item_id),
            },
            now=now,
        )
        logger.info("ops: requeued job=%s by admin=%s", job_id, actor_user_id)
        return job.model_copy(update={"max_attempts": new_max})

    # --- F-5 admin brand view --------------------------------------------------------------
    async def admin_brand(self, brand_id: UUID) -> AdminBrandView:
        view = await self._repo.admin_brand(brand_id)
        if view is None:
            raise BrandNotFoundError()
        return view

    async def admin_wa_summary(self, brand_id: UUID) -> AdminWaSummary:
        summary = await self._repo.admin_wa_summary(brand_id)
        if summary is None:
            raise BrandNotFoundError("no whatsapp account for brand")
        return summary

    # --- A-4 alerts ------------------------------------------------------------------------
    async def admin_alerts(self, *, kind: str, limit: int) -> list[dict[str, Any]]:
        return await self._repo.admin_alerts(kind=kind, limit=limit)


__all__ = ["PLATFORM_FLAGS", "OpsService", "PlatformFlags"]
