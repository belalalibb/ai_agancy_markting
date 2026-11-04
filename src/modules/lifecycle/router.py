"""Lifecycle routes (SPRINT-13 Part C, D-081) — OWNER only, fresh session.

| # | Method | Path                                      | Code(s)                              |
|---|--------|-------------------------------------------|--------------------------------------|
| 1 | POST   | /brands/{brand_id}/export                 | 202 + export_id · 413 export_too_large |
| 2 | GET    | /brands/{brand_id}/export/{export_id}     | JSON status, or the ZIP when ready   |
| 3 | DELETE | /brands/{brand_id}                        | 204 · 422 confirmation_mismatch      |

The Meta unsubscribe on delete is **best effort** over the app's single outbound client
(D-068) — a failure is recorded in ``admin_actions.after.unsubscribe`` and never blocks.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.core.database import get_db_session
from src.core.security import TokenEncryptionService
from src.modules.auth.dependencies import CurrentUserFresh
from src.modules.lifecycle.domain import BrandExport
from src.modules.lifecycle.exceptions import LifecycleError
from src.modules.lifecycle.infrastructure.postgres_repository import PgLifecycleRepository
from src.modules.lifecycle.service import LifecycleService, Unsubscriber
from src.modules.ops.audit import Audit
from src.modules.tenancy.dependencies import get_brand_access_resolver
from src.modules.tenancy.domain.access import BrandAccessResolver, BrandRole
from src.modules.tenancy.exceptions import BrandNotFoundError, InsufficientRoleError
from src.modules.tenancy.router import to_http as tenancy_to_http

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Data lifecycle (SPRINT-13, D-081)"])

SettingsDep = Annotated[Settings, Depends(get_settings)]
Resolver = Annotated[BrandAccessResolver, Depends(get_brand_access_resolver)]


def get_lifecycle_repository(session: Annotated[AsyncSession, Depends(get_db_session)]) -> Any:
    return PgLifecycleRepository(session)


def get_unsubscriber(request: Request, settings: SettingsDep) -> Unsubscriber | None:
    """``DELETE /{page_id}/subscribed_apps`` over ``app.state.http_client``; ``None`` when the
    process has no outbound client (unit app) ⇒ the delete records ``unsubscribe=skipped``."""
    client = getattr(request.app.state, "http_client", None)
    if client is None or settings.token_encryption_key is None:  # F-4 (S13-3)
        return None
    encryption = TokenEncryptionService(settings.token_encryption_key.get_secret_value())
    base = f"{settings.meta_graph_base_url}/{settings.meta_graph_api_version}"

    async def unsubscribe(page_id: str, encrypted_token: str) -> bool:
        try:
            token = encryption.decrypt_token(encrypted_token)
        except Exception:  # noqa: BLE001 - undecryptable token ⇒ ``unsubscribe=failed``
            return False
        try:
            res = await client.delete(
                f"{base}/{page_id}/subscribed_apps", params={"access_token": token}
            )
        finally:
            del token
        return res.status_code < 400

    return unsubscribe


def get_lifecycle_service(
    repo: Annotated[Any, Depends(get_lifecycle_repository)],
    audit: Audit,
    unsubscriber: Annotated[Unsubscriber | None, Depends(get_unsubscriber)],
    settings: SettingsDep,
) -> LifecycleService:
    return LifecycleService(
        repo,
        audit=audit.record,
        unsubscriber=unsubscriber,
        export_ttl_hours=settings.retention_export_hours,
    )


Service = Annotated[LifecycleService, Depends(get_lifecycle_service)]


def to_http(exc: LifecycleError) -> HTTPException:
    return HTTPException(exc.status, detail={"error": exc.error, "message": exc.message})


async def _require_owner(resolver: BrandAccessResolver, tenant_id: UUID, brand_id: UUID) -> None:
    role = await resolver.resolve(tenant_id, brand_id)
    if role is None:
        raise tenancy_to_http(BrandNotFoundError())
    if role is not BrandRole.OWNER:
        raise tenancy_to_http(InsufficientRoleError())


class ExportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    export_id: UUID
    brand_id: UUID
    status: str
    created_at: Any
    ready_at: Any
    sha256: str | None
    byte_size: int | None

    @classmethod
    def from_domain(cls, e: BrandExport) -> ExportOut:
        return cls(
            export_id=e.id,
            brand_id=e.brand_id,
            status=e.status,
            created_at=e.created_at,
            ready_at=e.ready_at,
            sha256=e.sha256,
            byte_size=e.byte_size,
        )


class DeleteConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    confirm: str = Field(min_length=1, max_length=255)


@router.post(
    "/brands/{brand_id}/export",
    response_model=ExportOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Export the brand's data as a ZIP (OWNER, fresh session) — D-081",
)
async def request_export(
    brand_id: UUID, actor: CurrentUserFresh, resolver: Resolver, service: Service
) -> ExportOut:
    await _require_owner(resolver, actor.tenant_id, brand_id)
    try:
        export = await service.export_brand(brand_id, requested_by=actor.user_id)
    except LifecycleError as exc:
        raise to_http(exc) from exc
    return ExportOut.from_domain(export)


@router.get(
    "/brands/{brand_id}/export/{export_id}",
    summary="Export status, or the ZIP itself once ready (OWNER, fresh session)",
    responses={200: {"content": {"application/zip": {}, "application/json": {}}}},
)
async def get_export(
    brand_id: UUID,
    export_id: UUID,
    actor: CurrentUserFresh,
    resolver: Resolver,
    service: Service,
    download: bool = True,
) -> Response:
    await _require_owner(resolver, actor.tenant_id, brand_id)
    try:
        export, blob = await service.get_export(brand_id, export_id)
    except LifecycleError as exc:
        raise to_http(exc) from exc
    if export.status != "ready" or blob is None or not download:
        return Response(
            ExportOut.from_domain(export).model_dump_json(), media_type="application/json"
        )
    return Response(
        blob,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="brand-export-{export.id}.zip"',
            "X-Export-SHA256": export.sha256 or "",
        },
    )


@router.delete(
    "/brands/{brand_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete the brand and everything under it (OWNER, fresh session, confirm) — D-081",
)
async def delete_brand(
    brand_id: UUID,
    payload: DeleteConfirm,
    actor: CurrentUserFresh,
    resolver: Resolver,
    service: Service,
) -> Response:
    await _require_owner(resolver, actor.tenant_id, brand_id)
    try:
        await service.delete_brand(brand_id, confirm=payload.confirm, actor_user_id=actor.user_id)
    except LifecycleError as exc:
        raise to_http(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["get_lifecycle_repository", "get_lifecycle_service", "get_unsubscriber", "router"]
