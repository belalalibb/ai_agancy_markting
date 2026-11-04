"""Ops admin routes (SPRINT-13 Part A) — all ``PlatformAdmin`` (fresh session, allow-list).

| # | Method | Path                                           | Act / audit row              |
|---|--------|------------------------------------------------|------------------------------|
| 1 | GET    | /admin/actions?target_kind=&target_id=&limit=  | read (≤ 200, newest first)   |
| 2 | POST   | /admin/brands/{brand_id}/suspend               | ``brand_suspended``          |
| 3 | POST   | /admin/brands/{brand_id}/unsuspend             | ``brand_unsuspended``        |
| 4 | GET    | /admin/flags                                   | read (DB first, Settings)    |
| 5 | PUT    | /admin/flags/{key}                             | ``flag_set``                 |
| 6 | GET    | /admin/publishing/dead-letter?limit=           | read                         |
| 7 | POST   | /admin/publishing/jobs/{job_id}/requeue        | ``job_requeued`` (+ D-055)   |
| 8 | GET    | /admin/alerts?kind=meta_token_expiring&limit=  | read                         |
| 9 | GET    | /admin/brands/{brand_id}                       | read (F-5, ids + suspended)  |
| 10| GET    | /admin/brands/{brand_id}/whatsapp/summary      | read (F-5, no secrets)       |

Non-admins get **404 ``not_found``** (§3-6: the panel's existence is not disclosed) — the
``PlatformAdmin`` 403 is remapped here in ``ops``; ``src/modules/auth`` is untouched.
The audit row for the catalog / entitlements admin acts is written by those routers'
``OpsAudit`` hook (``record_admin_action``) on the same request session.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.core.database import get_db_session
from src.modules.auth.dependencies import (
    AuthenticatedActor,
    CurrentUserFresh,
    require_platform_admin,
)
from src.modules.ops.domain import (
    ADMIN_ACTIONS_MAX,
    FLAG_KEYS,
    AdminAction,
    AdminBrandView,
    BrandSuspension,
    DeadLetterJob,
    PlatformFlag,
)
from src.modules.ops.exceptions import OpsError
from src.modules.ops.infrastructure.postgres_repository import PgOpsRepository
from src.modules.ops.service import OpsService

router = APIRouter(prefix="/admin", tags=["Platform admin — Day-2 Ops (SPRINT-13)"])

ADMIN_ALERT_KINDS = ("meta_token_expiring", "wa_monthly_service_80pct", "wa_quality_degraded")


def get_ops_service(session: Annotated[AsyncSession, Depends(get_db_session)]) -> OpsService:
    return OpsService(PgOpsRepository(session))


Service = Annotated[OpsService, Depends(get_ops_service)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def require_ops_admin(actor: CurrentUserFresh, settings: SettingsDep) -> AuthenticatedActor:
    """§3-6: a fresh non-admin session answers **404** on every new ``/admin/*`` route —
    the auth guard's 403 is remapped here (no change in ``src/modules/auth``)."""
    try:
        return await require_platform_admin(actor, settings)
    except HTTPException as exc:
        if exc.status_code != status.HTTP_403_FORBIDDEN:
            raise
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"error": "not_found", "message": "not found"}
        ) from None


OpsAdmin = Annotated[AuthenticatedActor, Depends(require_ops_admin)]


def to_http(exc: OpsError) -> HTTPException:
    return HTTPException(exc.status, detail={"error": exc.error, "message": exc.message})


# ------------------------------------------------------------------- schemas --
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

    @classmethod
    def from_domain(cls, a: AdminAction) -> AdminActionOut:
        return cls.model_validate(a.model_dump())


class FlagOut(BaseModel):
    key: str
    value: bool
    source: str
    updated_at: datetime | None

    @classmethod
    def from_domain(cls, f: PlatformFlag) -> FlagOut:
        return cls(key=f.key, value=f.value, source=f.source, updated_at=f.updated_at)


class FlagPut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: bool


class SuspensionOut(BaseModel):
    brand_id: UUID
    suspended: bool
    suspended_at: datetime | None

    @classmethod
    def from_domain(cls, s: BrandSuspension) -> SuspensionOut:
        return cls(
            brand_id=s.brand_id, suspended=s.suspended_at is not None, suspended_at=s.suspended_at
        )


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

    @classmethod
    def from_domain(cls, j: DeadLetterJob) -> DeadLetterOut:
        return cls.model_validate(j.model_dump())


class AdminBrandOut(BaseModel):
    """F-5: what the admin entitlements card needs — ids + suspension, nothing else."""

    id: UUID
    name: str
    slug: str
    suspended: bool
    suspended_at: datetime | None
    owner_tenant_id: UUID

    @classmethod
    def from_domain(cls, b: AdminBrandView) -> AdminBrandOut:
        return cls(
            id=b.id,
            name=b.name,
            slug=b.slug,
            suspended=b.suspended,
            suspended_at=b.suspended_at,
            owner_tenant_id=b.owner_tenant_id,
        )


class AdminWaSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    display_phone: str | None
    status: str
    quality_rating: str | None
    messaging_limit: str | None


class AdminAlertOut(BaseModel):
    id: UUID
    brand_id: UUID
    kind: str
    severity: str
    payload: dict[str, Any]
    created_at: datetime
    acknowledged_at: datetime | None


Limit = Annotated[int, Query(ge=1, le=ADMIN_ACTIONS_MAX)]


# -------------------------------------------------------------------- routes --
@router.get("/actions", response_model=list[AdminActionOut], summary="Admin audit log (A-1)")
async def list_actions(
    _: OpsAdmin,
    service: Service,
    target_kind: Annotated[str | None, Query(max_length=32)] = None,
    target_id: Annotated[str | None, Query(max_length=64)] = None,
    limit: Limit = 50,
) -> list[AdminActionOut]:
    rows = await service.list_actions(target_kind=target_kind, target_id=target_id, limit=limit)
    return [AdminActionOut.from_domain(r) for r in rows]


@router.post(
    "/brands/{brand_id}/suspend", response_model=SuspensionOut, summary="Suspend a brand (A-2)"
)
async def suspend_brand(brand_id: UUID, actor: OpsAdmin, service: Service) -> SuspensionOut:
    try:
        state = await service.set_suspended(brand_id, suspended=True, actor_user_id=actor.user_id)
    except OpsError as exc:
        raise to_http(exc) from exc
    return SuspensionOut.from_domain(state)


@router.post(
    "/brands/{brand_id}/unsuspend", response_model=SuspensionOut, summary="Lift a suspension"
)
async def unsuspend_brand(brand_id: UUID, actor: OpsAdmin, service: Service) -> SuspensionOut:
    try:
        state = await service.set_suspended(brand_id, suspended=False, actor_user_id=actor.user_id)
    except OpsError as exc:
        raise to_http(exc) from exc
    return SuspensionOut.from_domain(state)


@router.get("/flags", response_model=list[FlagOut], summary="Platform kill switches (A-2)")
async def list_flags(_: OpsAdmin, service: Service, settings: SettingsDep) -> list[FlagOut]:
    return [FlagOut.from_domain(f) for f in await service.flags(settings)]


@router.put(
    "/flags/{key}",
    response_model=FlagOut,
    summary="Set a platform flag — " + ", ".join(FLAG_KEYS),
    responses={404: {"description": "flag_unknown"}},
)
async def put_flag(
    key: str, payload: FlagPut, actor: OpsAdmin, service: Service, settings: SettingsDep
) -> FlagOut:
    try:
        stored = await service.set_flag(
            key, payload.value, actor_user_id=actor.user_id, settings=settings
        )
    except OpsError as exc:
        raise to_http(exc) from exc
    return FlagOut.from_domain(stored)


@router.get(
    "/publishing/dead-letter",
    response_model=list[DeadLetterOut],
    summary="Failed publish jobs, newest first (A-3)",
)
async def dead_letter(_: OpsAdmin, service: Service, limit: Limit = 50) -> list[DeadLetterOut]:
    return [DeadLetterOut.from_domain(j) for j in await service.dead_letter(limit=limit)]


@router.post(
    "/publishing/jobs/{job_id}/requeue",
    response_model=DeadLetterOut,
    summary="Requeue one failed job (CAS; attempt_count kept, max_attempts += 3)",
    responses={
        404: {"description": "publish_job_not_found"},
        409: {
            "description": "job_not_failed | manual_verification_required | "
            "publish_in_progress | item_not_publish_failed"
        },
    },
)
async def requeue_job(job_id: UUID, actor: OpsAdmin, service: Service) -> DeadLetterOut:
    try:
        job = await service.requeue(job_id, actor_user_id=actor.user_id)
    except OpsError as exc:
        raise to_http(exc) from exc
    return DeadLetterOut.from_domain(job)


@router.get(
    "/brands/{brand_id}",
    response_model=AdminBrandOut,
    summary="Admin brand card (F-5): ids + suspension, no membership needed",
    responses={404: {"description": "brand_not_found"}},
)
async def admin_brand(brand_id: UUID, _: OpsAdmin, service: Service) -> AdminBrandOut:
    try:
        return AdminBrandOut.from_domain(await service.admin_brand(brand_id))
    except OpsError as exc:
        raise to_http(exc) from exc


@router.get(
    "/brands/{brand_id}/whatsapp/summary",
    response_model=AdminWaSummaryOut,
    summary="Admin WhatsApp summary (F-5): id/phone/status/quality, no secrets",
    responses={404: {"description": "brand_not_found (no WhatsApp account)"}},
)
async def admin_wa_summary(brand_id: UUID, _: OpsAdmin, service: Service) -> AdminWaSummaryOut:
    try:
        return AdminWaSummaryOut.model_validate(await service.admin_wa_summary(brand_id))
    except OpsError as exc:
        raise to_http(exc) from exc


@router.get(
    "/alerts", response_model=list[AdminAlertOut], summary="Platform-wide Pulse alerts (A-4)"
)
async def admin_alerts(
    _: OpsAdmin,
    service: Service,
    kind: Annotated[str, Query(max_length=32)] = "meta_token_expiring",
    limit: Limit = 50,
) -> list[AdminAlertOut]:
    if kind not in ADMIN_ALERT_KINDS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error": "alert_kind_unknown",
                "message": f"kind must be one of {ADMIN_ALERT_KINDS}",
            },
        )
    return [AdminAlertOut(**row) for row in await service.admin_alerts(kind=kind, limit=limit)]


__all__ = ["ADMIN_ALERT_KINDS", "Service", "get_ops_service", "router", "to_http"]
