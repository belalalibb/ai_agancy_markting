"""``/api/v1/admin/*`` Day-2 routes (SPRINT-13 Part A) — every route is ``PlatformAdmin``.

| # | Method | Path                                          | Audit row (A-1)          |
|---|--------|-----------------------------------------------|--------------------------|
| 1 | GET    | /admin/actions?target_kind=&target_id=&limit= | —                        |
| 2 | POST   | /admin/brands/{brand_id}/suspend              | ``brand_suspended``      |
| 3 | POST   | /admin/brands/{brand_id}/unsuspend            | ``brand_unsuspended``    |
| 4 | PUT    | /admin/flags/{key}                            | ``flag_set``             |
| 5 | GET    | /admin/publishing/dead-letter?limit=          | —                        |
| 6 | POST   | /admin/publishing/jobs/{job_id}/requeue       | ``requeued_by_admin``    |
| 7 | GET    | /admin/alerts?kind=&limit=                    | —                        |

The audit row is written through the **request** session — the act and its row commit (or
roll back) together (criterion 7). Error bodies: ``{"error": "<code>", "message": …}``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.audit import AuditPort
from src.core.database import get_db_session
from src.modules.auth.dependencies import PlatformAdmin
from src.modules.ops.audit import MAX_LIST_LIMIT, AdminAction, AuditWriter, PgAuditWriter
from src.modules.ops.dead_letter import (
    MAX_DEAD_LETTER,
    DeadLetterJob,
    DeadLetterRepository,
    PgDeadLetterRepository,
)
from src.modules.ops.flags import (
    FLAG_KEYS,
    FlagCache,
    FlagRepository,
    PgFlagRepository,
    PlatformFlag,
    utcnow,
)

router = APIRouter(prefix="/admin", tags=["Platform admin"])

# One cache per process; the guard middleware shares it (``src/main.py``) and every admin
# write below invalidates it so a flipped switch is live on the next request, not in 30 s.
FLAG_CACHE = FlagCache()


# ---------------------------------------------------------------- dependencies --
def get_audit_writer(session: Annotated[AsyncSession, Depends(get_db_session)]) -> AuditWriter:
    return PgAuditWriter(session)


def get_flag_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> FlagRepository:
    return PgFlagRepository(session)


def get_dead_letter_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> DeadLetterRepository:
    return PgDeadLetterRepository(session)


Audit = Annotated[AuditWriter, Depends(get_audit_writer)]
Flags = Annotated[FlagRepository, Depends(get_flag_repository)]
DeadLetters = Annotated[DeadLetterRepository, Depends(get_dead_letter_repository)]


class AuditAdapter:
    """:class:`src.core.audit.AuditPort` over the request's :class:`AuditWriter` — bound in
    ``src/main.py`` so other modules' admin routes audit through the same session."""

    def __init__(self, writer: AuditWriter) -> None:
        self._writer = writer

    async def record(
        self,
        *,
        actor_user_id: UUID | None,
        action: str,
        target_kind: str,
        target_id: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        await self._writer.record(
            AdminAction(
                actor_user_id=actor_user_id,
                action=action,
                target_kind=target_kind,
                target_id=target_id,
                before=before or {},
                after=after or {},
            )
        )


def audit_port_dependency(writer: Audit) -> AuditPort:
    return AuditAdapter(writer)


# ---------------------------------------------------------------------- schemas --
class AdminActionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor_user_id: UUID | None
    action: str
    target_kind: str
    target_id: str
    before: dict[str, Any]
    after: dict[str, Any]
    at: datetime


class SuspendOut(BaseModel):
    brand_id: UUID
    suspended: bool
    suspended_at: datetime | None


class FlagIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: bool


class FlagOut(BaseModel):
    key: str
    value: bool
    updated_at: datetime


class DeadLetterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: UUID
    item_id: UUID
    brand_id: UUID
    channel: str
    error_class: str | None
    attempt_count: int
    max_attempts: int
    failed_at: datetime


class RequeueOut(DeadLetterOut):
    status: str = "queued"
    item_rescheduled: bool


class AdminAlertOut(BaseModel):
    id: UUID
    brand_id: UUID
    kind: str
    severity: str
    payload: dict[str, Any]
    created_at: datetime
    acknowledged_at: datetime | None


def _error(code: int, error: str, message: str) -> HTTPException:
    return HTTPException(code, detail={"error": error, "message": message})


# ------------------------------------------------------------------ A-1 actions --
@router.get("/actions", response_model=list[AdminActionOut], summary="Audit trail (newest first)")
async def list_actions(
    _: PlatformAdmin,
    audit: Audit,
    target_kind: str | None = None,
    target_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIST_LIMIT)] = 50,
) -> list[AdminActionOut]:
    rows = await audit.list_actions(target_kind=target_kind, target_id=target_id, limit=limit)
    return [AdminActionOut.model_validate(r) for r in rows]


# ------------------------------------------------------- A-2 suspend / flags --
async def _set_suspended(
    brand_id: UUID, actor: PlatformAdmin, flags: Flags, audit: Audit, *, suspend: bool
) -> SuspendOut:
    before = await flags.brand_suspended_at(brand_id)
    if before is False:
        raise _error(status.HTTP_404_NOT_FOUND, "brand_not_found", "brand not found")
    now = utcnow()
    new_value = now if suspend else None
    await flags.set_brand_suspended(brand_id, new_value)
    await audit.record(
        AdminAction(
            actor_user_id=actor.user_id,
            action="brand_suspended" if suspend else "brand_unsuspended",
            target_kind="brand",
            target_id=str(brand_id),
            before={"suspended_at": before.isoformat() if isinstance(before, datetime) else None},
            after={"suspended_at": new_value.isoformat() if new_value else None},
            at=now,
        )
    )
    FLAG_CACHE.invalidate()
    return SuspendOut(brand_id=brand_id, suspended=suspend, suspended_at=new_value)


@router.post(
    "/brands/{brand_id}/suspend",
    response_model=SuspendOut,
    summary="Suspend a brand: every write answers 403 brand_suspended (reads stay)",
)
async def suspend_brand(
    brand_id: UUID, actor: PlatformAdmin, flags: Flags, audit: Audit
) -> SuspendOut:
    return await _set_suspended(brand_id, actor, flags, audit, suspend=True)


@router.post("/brands/{brand_id}/unsuspend", response_model=SuspendOut, summary="Lift suspension")
async def unsuspend_brand(
    brand_id: UUID, actor: PlatformAdmin, flags: Flags, audit: Audit
) -> SuspendOut:
    return await _set_suspended(brand_id, actor, flags, audit, suspend=False)


@router.get("/flags", response_model=list[FlagOut], summary="Platform flags stored in the DB")
async def list_flags(_: PlatformAdmin, flags: Flags) -> list[FlagOut]:
    stored = await flags.all_flags()
    now = utcnow()
    return [FlagOut(key=k, value=v, updated_at=now) for k, v in sorted(stored.items())]


@router.put(
    "/flags/{key}",
    response_model=FlagOut,
    summary="Set platform_readonly / feature_pulse / feature_lead_loop (DB beats env)",
)
async def put_flag(
    key: str, payload: FlagIn, actor: PlatformAdmin, flags: Flags, audit: Audit
) -> FlagOut:
    if key not in FLAG_KEYS:
        raise _error(status.HTTP_404_NOT_FOUND, "flag_not_found", "unknown flag")
    before = (await flags.all_flags()).get(key)
    now = utcnow()
    flag: PlatformFlag = await flags.set_flag(key, payload.value, now=now)
    await audit.record(
        AdminAction(
            actor_user_id=actor.user_id,
            action="flag_set",
            target_kind="flag",
            target_id=key,
            before={"value": before},
            after={"value": flag.value},
            at=now,
        )
    )
    FLAG_CACHE.invalidate()
    return FlagOut(key=flag.key, value=flag.value, updated_at=flag.updated_at)


# ------------------------------------------------------------- A-3 dead-letter --
@router.get(
    "/publishing/dead-letter",
    response_model=list[DeadLetterOut],
    summary="Failed publish jobs (newest first)",
)
async def dead_letter(
    _: PlatformAdmin,
    jobs: DeadLetters,
    limit: Annotated[int, Query(ge=1, le=MAX_DEAD_LETTER)] = 50,
) -> list[DeadLetterOut]:
    return [DeadLetterOut.model_validate(j) for j in await jobs.list_failed(limit=limit)]


@router.post(
    "/publishing/jobs/{job_id}/requeue",
    response_model=RequeueOut,
    summary="Requeue a failed job (attempt_count=0, next_attempt_at=now; item ⇒ scheduled)",
    responses={409: {"description": "job_not_failed"}},
)
async def requeue_job(
    job_id: UUID, actor: PlatformAdmin, jobs: DeadLetters, audit: Audit
) -> RequeueOut:
    now = utcnow()
    job: DeadLetterJob | None = await jobs.requeue(job_id, now=now)
    if job is None:
        raise _error(status.HTTP_409_CONFLICT, "job_not_failed", "job is not in failed state")
    revision_id = getattr(jobs, "last_revision_id", None) or job_id
    rescheduled = await jobs.reschedule_item(job.item_id, revision_id, now=now)
    await audit.record(
        AdminAction(
            actor_user_id=actor.user_id,
            action="requeued_by_admin",
            target_kind="publish_job",
            target_id=str(job_id),
            before={"status": "failed", "attempt_count": job.attempt_count},
            after={
                "status": "queued",
                "attempt_count": 0,
                "item_id": str(job.item_id),
                "item_rescheduled": rescheduled,
            },
            at=now,
        )
    )
    return RequeueOut(**job.model_dump(), item_rescheduled=rescheduled)


# ------------------------------------------------------------------ A-4 alerts --
_ALERTS = text(
    "SELECT id, brand_id, kind, severity, payload, created_at, acknowledged_at "
    "FROM pulse_alerts WHERE (:kind IS NULL OR kind = :kind) "
    "ORDER BY created_at DESC LIMIT :limit"
)


@router.get(
    "/alerts",
    response_model=list[AdminAlertOut],
    summary="Platform-wide alert digest (default kind: meta_token_expiring)",
)
async def admin_alerts(
    _: PlatformAdmin,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    kind: str | None = "meta_token_expiring",
    limit: Annotated[int, Query(ge=1, le=MAX_LIST_LIMIT)] = 50,
) -> list[AdminAlertOut]:
    rows = await session.execute(_ALERTS, {"kind": kind or None, "limit": limit})
    return [
        AdminAlertOut(
            id=r[0],
            brand_id=r[1],
            kind=r[2],
            severity=r[3],
            payload=dict(r[4] or {}),
            created_at=r[5],
            acknowledged_at=r[6],
        )
        for r in rows.all()
    ]


__all__ = ["FLAG_CACHE", "AuditAdapter", "audit_port_dependency", "router"]
