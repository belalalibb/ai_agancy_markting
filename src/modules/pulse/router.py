"""Pulse routes (SPRINT-12 B-3): list open alerts and acknowledge one.

``GET  /brands/{brand_id}/pulse/alerts?open=true`` — every brand role (READ_ROLES).
``POST /brands/{brand_id}/pulse/alerts/{alert_id}/ack`` — OWNER / MANAGER.
``FEATURE_PULSE=false`` ⇒ both routes answer 404 (``pulse_disabled``) and ``src/main.py``
does not even mount the router — see REMOVABILITY.md.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.core.database import get_db_session
from src.modules.auth.dependencies import CurrentUser
from src.modules.pulse.domain import Alert, PulseRepository
from src.modules.pulse.infrastructure.postgres_repository import PgPulseRepository
from src.modules.tenancy.dependencies import get_brand_access_resolver
from src.modules.tenancy.domain.access import (
    CONNECTION_WRITE_ROLES,
    READ_ROLES,
    BrandAccessResolver,
    BrandRole,
)
from src.modules.tenancy.exceptions import BrandNotFoundError, InsufficientRoleError, TenancyError
from src.modules.tenancy.router import to_http as tenancy_to_http

router = APIRouter(prefix="/brands/{brand_id}/pulse", tags=["Pulse"])

MAX_LIMIT = 200


class AlertPublic(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    brand_id: UUID
    kind: str
    severity: str
    payload: dict[str, Any]
    created_at: datetime
    acknowledged_at: datetime | None
    acknowledged_by_user_id: UUID | None

    @classmethod
    def from_domain(cls, alert: Alert) -> AlertPublic:
        return cls(**alert.model_dump())


class AlertPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[AlertPublic]
    open_count: int


def get_pulse_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PulseRepository:
    return PgPulseRepository(session)


Repo = Annotated[PulseRepository, Depends(get_pulse_repository)]
Resolver = Annotated[BrandAccessResolver, Depends(get_brand_access_resolver)]


def _ensure_enabled(settings: Settings) -> None:
    if not settings.feature_pulse:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"error": "pulse_disabled", "message": "Pulse is disabled (FEATURE_PULSE)"},
        )


async def _authorise(
    resolver: BrandAccessResolver, tenant_id: UUID, brand_id: UUID, allowed: frozenset[BrandRole]
) -> BrandRole:
    try:
        role = await resolver.resolve(tenant_id, brand_id)
        if role is None:
            raise BrandNotFoundError()
        if role not in allowed:
            raise InsufficientRoleError(f"role {role.value!r} may not perform this action")
    except TenancyError as exc:
        raise tenancy_to_http(exc) from exc
    return role


@router.get("/alerts", response_model=AlertPage, summary="List Pulse alerts of a brand")
async def list_alerts(
    brand_id: UUID,
    actor: CurrentUser,
    resolver: Resolver,
    repo: Repo,
    settings: Annotated[Settings, Depends(get_settings)],
    open: Annotated[bool, Query(description="Only alerts not yet acknowledged")] = True,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
) -> AlertPage:
    _ensure_enabled(settings)
    await _authorise(resolver, actor.tenant_id, brand_id, READ_ROLES)
    alerts = await repo.list_alerts(brand_id, open_only=open, limit=limit)
    return AlertPage(
        items=[AlertPublic.from_domain(a) for a in alerts],
        open_count=sum(1 for a in alerts if a.is_open),
    )


@router.post(
    "/alerts/{alert_id}/ack", response_model=AlertPublic, summary="Acknowledge a Pulse alert"
)
async def acknowledge_alert(
    brand_id: UUID,
    alert_id: UUID,
    actor: CurrentUser,
    resolver: Resolver,
    repo: Repo,
    settings: Annotated[Settings, Depends(get_settings)],
) -> AlertPublic:
    _ensure_enabled(settings)
    await _authorise(resolver, actor.tenant_id, brand_id, CONNECTION_WRITE_ROLES)
    alert = await repo.acknowledge(
        brand_id, alert_id, actor_user_id=actor.user_id, now=datetime.now(tz=UTC)
    )
    if alert is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"error": "pulse_alert_not_found", "message": "alert not found"},
        )
    return AlertPublic.from_domain(alert)


__all__ = ["AlertPage", "AlertPublic", "get_pulse_repository", "router"]
