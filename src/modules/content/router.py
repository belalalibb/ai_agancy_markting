"""HTTP surface of ``content`` — ``/api/v1/brands/{brand_id}/content/...`` (SPRINT-06 B3).

Visibility table (directive §2-0 b, never weakened):

=====================  ======  ==============================  ======================
action                 OWNER   MANAGER                         VIEWER
=====================  ======  ==============================  ======================
read items/log         ✅      ✅                              ✅
create / edit body     ✅      ✅                              403 ``insufficient_role``
submit / withdraw      ✅      ✅                              403
approve / reject       ✅      403 ``approval_requires_owner`` 403
schedule / unschedule  ✅      ✅                              403
archive                ✅      own items only                  403
generation_log         ✅      ✅                              field removed
=====================  ======  ==============================  ======================

Writes use ``CurrentUserFresh`` (session re-checked), reads ``CurrentUser``. Invalid lifecycle
moves answer **409** ``{"error": "invalid_transition", "from": ..., "action": ...}``.
"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db_session
from src.modules.ai_gateway.dependencies import Gateway
from src.modules.ai_gateway.exceptions import AIRateLimitedError, AIUpstreamError
from src.modules.auth.dependencies import CurrentUser, CurrentUserFresh, get_rate_limiter
from src.modules.auth.domain.rate_limit import RateLimiter
from src.modules.content.domain.lifecycle import Action
from src.modules.content.domain.models import Channel, ContentItem, ContentStatus
from src.modules.content.domain.repository import ContentRepository
from src.modules.content.exceptions import (
    ContentError,
    GenerationRateLimitedError,
    InvalidTransitionError,
)
from src.modules.content.infrastructure.postgres_repository import PostgresContentRepository
from src.modules.content.schemas import (
    ApprovalEventPublic,
    ApprovalNoteRequest,
    ContentBodyUpdate,
    ContentItemCreate,
    ContentItemDetail,
    ContentItemPage,
    ContentItemPublic,
    ContentRevisionPublic,
    GenerateRequest,
    RejectRequest,
    ScheduleRequest,
    StatusLiteral,
)
from src.modules.content.service import ContentService
from src.modules.social_connect.domain.repository import ConnectedAccountRepository
from src.modules.social_connect.router import get_repository as get_connection_repository
from src.modules.tenancy.dependencies import get_brand_access_resolver
from src.modules.tenancy.domain.access import (
    CONNECTION_WRITE_ROLES,
    READ_ROLES,
    BrandAccessResolver,
    BrandRole,
)
from src.modules.tenancy.exceptions import BrandNotFoundError, InsufficientRoleError, TenancyError
from src.modules.tenancy.router import to_http as tenancy_to_http

router = APIRouter(tags=["Content — drafts & approval"])

BRAND_PREFIX = "/brands/{brand_id}/content"
MAX_LIST_LIMIT = 100


# ------------------------------------------------------------ dependencies --
def get_content_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ContentRepository:
    return PostgresContentRepository(session)


def get_content_service(
    repository: Annotated[ContentRepository, Depends(get_content_repository)],
    connections: Annotated[ConnectedAccountRepository, Depends(get_connection_repository)],
) -> ContentService:
    return ContentService(repository, connections)


Service = Annotated[ContentService, Depends(get_content_service)]
Resolver = Annotated[BrandAccessResolver, Depends(get_brand_access_resolver)]
LimitQuery = Annotated[int, Query(ge=1, le=MAX_LIST_LIMIT)]
CursorQuery = Annotated[str | None, Query(max_length=128)]
StatusQuery = Annotated[StatusLiteral | None, Query(alias="status")]


async def _authorise(
    resolver: BrandAccessResolver, tenant_id: UUID, brand_id: UUID, allowed: frozenset[BrandRole]
) -> BrandRole:
    """Same policy as ``social_connect``: no access ⇒ 404 ``brand_not_found``; wrong role ⇒
    403 ``insufficient_role``."""
    try:
        role = await resolver.resolve(tenant_id, brand_id)
        if role is None:
            raise BrandNotFoundError()
        if role not in allowed:
            raise InsufficientRoleError(f"role {role.value!r} may not perform this action")
    except TenancyError as exc:
        raise tenancy_to_http(exc) from exc
    return role


def to_http(exc: ContentError) -> HTTPException:
    detail: dict[str, object] = {"error": exc.error, "message": exc.message}
    if isinstance(exc, InvalidTransitionError):
        detail["from"] = exc.from_status.value
        detail["action"] = exc.action
    headers = (
        {"Retry-After": str(exc.retry_after_seconds)}
        if isinstance(exc, GenerationRateLimitedError)
        else None
    )
    return HTTPException(exc.http_status, detail=detail, headers=headers)


Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]


# ------------------------------------------------------------------ cursor --
def _encode_cursor(item: ContentItem) -> str:
    raw = f"{item.created_at.isoformat()}|{item.id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[datetime, UUID] | None:
    if cursor is None:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        created_at, item_id = base64.urlsafe_b64decode(padded).decode().split("|", 1)
        return datetime.fromisoformat(created_at), UUID(item_id)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "invalid_cursor", "message": "cursor is not valid"},
        ) from exc


async def _public(service: ContentService, item: ContentItem) -> ContentItemPublic:
    return ContentItemPublic.from_domain(item, current_body=await service.current_body(item))


# ------------------------------------------------------------------- routes --
@router.post(
    f"{BRAND_PREFIX}/items",
    response_model=ContentItemPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a draft (revision 1, provenance=human) — OWNER/MANAGER",
    responses={403: {"description": "insufficient_role"}, 404: {"description": "brand_not_found"}},
)
async def create_item(
    brand_id: UUID,
    payload: ContentItemCreate,
    actor: CurrentUserFresh,
    service: Service,
    resolver: Resolver,
) -> ContentItemPublic:
    """Roles: OWNER, MANAGER (VIEWER ⇒ 403 ``insufficient_role``). Result: ``draft``."""
    await _authorise(resolver, actor.tenant_id, brand_id, CONNECTION_WRITE_ROLES)
    item = await service.create_item(
        brand_id,
        actor_tenant_id=actor.tenant_id,
        channel=Channel(payload.channel),
        body=payload.body,
        target_page_id=payload.target_page_id,
    )
    return ContentItemPublic.from_domain(item, current_body=payload.body)


@router.get(
    f"{BRAND_PREFIX}/items",
    response_model=ContentItemPage,
    summary="List content items (keyset on created_at,id; newest first) — all roles",
)
async def list_items(
    brand_id: UUID,
    actor: CurrentUser,
    service: Service,
    resolver: Resolver,
    status_filter: StatusQuery = None,
    limit: LimitQuery = 25,
    cursor: CursorQuery = None,
) -> ContentItemPage:
    """Roles: OWNER, MANAGER, VIEWER. ``?status=`` filters; ``limit`` ≤ 100; ``cursor`` is
    opaque (``next_cursor`` of the previous page)."""
    await _authorise(resolver, actor.tenant_id, brand_id, READ_ROLES)
    items, has_more = await service.list_items(
        brand_id,
        status=ContentStatus(status_filter) if status_filter else None,
        limit=limit,
        cursor=_decode_cursor(cursor),
    )
    return ContentItemPage(
        items=[await _public(service, i) for i in items],
        next_cursor=_encode_cursor(items[-1]) if has_more and items else None,
        has_more=has_more,
    )


@router.get(
    f"{BRAND_PREFIX}/items/{{item_id}}",
    response_model=ContentItemDetail,
    response_model_exclude_none=True,
    summary="Item detail with last 20 revisions and the approval log — all roles",
    responses={404: {"description": "content_not_found / brand_not_found"}},
)
async def get_item(
    brand_id: UUID, item_id: UUID, actor: CurrentUser, service: Service, resolver: Resolver
) -> ContentItemDetail:
    """Roles: OWNER, MANAGER, VIEWER. ``generation_log`` (provider/model/token usage of the AI
    revisions) is present for OWNER/MANAGER and **removed** for VIEWER (§2-0 b)."""
    role = await _authorise(resolver, actor.tenant_id, brand_id, READ_ROLES)
    try:
        item, revisions, events = await service.get_detail(brand_id, item_id)
    except ContentError as exc:
        raise to_http(exc) from exc
    generation_log = (
        [
            {k: v for k, v in r.provenance.items() if k != "kind"} | {"revision_no": r.revision_no}
            for r in revisions
            if r.provenance.get("kind") == "ai_generated"
        ]
        if role is not BrandRole.VIEWER
        else None
    )
    return ContentItemDetail(
        **ContentItemPublic.from_domain(
            item, current_body=revisions[0].body if revisions else None
        ).model_dump(),
        revisions=[ContentRevisionPublic.from_domain(r) for r in revisions],
        approval_events=[ApprovalEventPublic.from_domain(e) for e in events],
        generation_log=generation_log,
    )


@router.put(
    f"{BRAND_PREFIX}/items/{{item_id}}/body",
    response_model=ContentItemPublic,
    summary="New revision of the body — OWNER/MANAGER",
    responses={
        403: {"description": "insufficient_role"},
        404: {"description": "content_not_found"},
        409: {"description": "invalid_transition (archived items are immutable)"},
    },
)
async def update_body(
    brand_id: UUID,
    item_id: UUID,
    payload: ContentBodyUpdate,
    actor: CurrentUserFresh,
    service: Service,
    resolver: Resolver,
) -> ContentItemPublic:
    """Roles: OWNER, MANAGER. From ``pending_approval``/``approved``/``rejected``/``scheduled``
    the item returns to ``draft`` and an automatic ``withdrawn`` event is logged (B2-U2)."""
    await _authorise(resolver, actor.tenant_id, brand_id, CONNECTION_WRITE_ROLES)
    try:
        item = await service.update_body(
            brand_id,
            item_id,
            body=payload.body,
            actor_tenant_id=actor.tenant_id,
            actor_user_id=actor.user_id,
        )
    except ContentError as exc:
        raise to_http(exc) from exc
    return ContentItemPublic.from_domain(item, current_body=payload.body)


async def _act(
    service: ContentService,
    resolver: BrandAccessResolver,
    brand_id: UUID,
    item_id: UUID,
    action: Action,
    actor_tenant_id: UUID,
    actor_user_id: UUID,
    *,
    note: str | None = None,
    scheduled_for: datetime | None = None,
    target_page_id: str | None = None,
) -> ContentItemPublic:
    # Route admission: OWNER/MANAGER may *attempt* (VIEWER ⇒ generic 403); the lifecycle
    # decides owner-only actions (403 approval_requires_owner) and the state table (409).
    role = await _authorise(resolver, actor_tenant_id, brand_id, CONNECTION_WRITE_ROLES)
    try:
        item = await service.act(
            brand_id,
            item_id,
            action,
            actor_role=role,
            actor_tenant_id=actor_tenant_id,
            actor_user_id=actor_user_id,
            note=note,
            scheduled_for=scheduled_for,
            target_page_id=target_page_id,
        )
    except ContentError as exc:
        raise to_http(exc) from exc
    return await _public(service, item)


_TRANSITION_RESPONSES = {
    403: {"description": "insufficient_role / approval_requires_owner"},
    404: {"description": "content_not_found"},
    409: {"description": "invalid_transition {from, action}"},
}


@router.post(
    f"{BRAND_PREFIX}/items/{{item_id}}/submit",
    response_model=ContentItemPublic,
    summary="draft → pending_approval — OWNER/MANAGER",
    responses=_TRANSITION_RESPONSES,
)
async def submit_item(
    brand_id: UUID,
    item_id: UUID,
    actor: CurrentUserFresh,
    service: Service,
    resolver: Resolver,
    payload: ApprovalNoteRequest | None = None,
) -> ContentItemPublic:
    """Roles: OWNER, MANAGER. Logs a ``submitted`` event."""
    return await _act(
        service,
        resolver,
        brand_id,
        item_id,
        Action.SUBMIT,
        actor.tenant_id,
        actor.user_id,
        note=payload.note if payload else None,
    )


@router.post(
    f"{BRAND_PREFIX}/items/{{item_id}}/withdraw",
    response_model=ContentItemPublic,
    summary="pending_approval → draft — OWNER/MANAGER",
    responses=_TRANSITION_RESPONSES,
)
async def withdraw_item(
    brand_id: UUID,
    item_id: UUID,
    actor: CurrentUserFresh,
    service: Service,
    resolver: Resolver,
    payload: ApprovalNoteRequest | None = None,
) -> ContentItemPublic:
    """Roles: OWNER, MANAGER. Logs a ``withdrawn`` event."""
    return await _act(
        service,
        resolver,
        brand_id,
        item_id,
        Action.WITHDRAW,
        actor.tenant_id,
        actor.user_id,
        note=payload.note if payload else None,
    )


@router.post(
    f"{BRAND_PREFIX}/items/{{item_id}}/approve",
    response_model=ContentItemPublic,
    summary="pending_approval → approved — OWNER only (D-055)",
    responses=_TRANSITION_RESPONSES,
)
async def approve_item(
    brand_id: UUID,
    item_id: UUID,
    actor: CurrentUserFresh,
    service: Service,
    resolver: Resolver,
    payload: ApprovalNoteRequest | None = None,
) -> ContentItemPublic:
    """Roles: **OWNER only** — MANAGER ⇒ 403 ``approval_requires_owner``. An owner may approve
    their own draft. Logs an ``approved`` event."""
    return await _act(
        service,
        resolver,
        brand_id,
        item_id,
        Action.APPROVE,
        actor.tenant_id,
        actor.user_id,
        note=payload.note if payload else None,
    )


@router.post(
    f"{BRAND_PREFIX}/items/{{item_id}}/reject",
    response_model=ContentItemPublic,
    summary="pending_approval → rejected (note ≥ 3 chars) — OWNER only (D-055)",
    responses={**_TRANSITION_RESPONSES, 422: {"description": "reject_note_required"}},
)
async def reject_item(
    brand_id: UUID,
    item_id: UUID,
    payload: RejectRequest,
    actor: CurrentUserFresh,
    service: Service,
    resolver: Resolver,
) -> ContentItemPublic:
    """Roles: **OWNER only** — MANAGER ⇒ 403 ``approval_requires_owner``. ``note`` is
    mandatory (≥ 3 chars, else 422). Logs a ``rejected`` event carrying the note."""
    return await _act(
        service,
        resolver,
        brand_id,
        item_id,
        Action.REJECT,
        actor.tenant_id,
        actor.user_id,
        note=payload.note,
    )


@router.post(
    f"{BRAND_PREFIX}/items/{{item_id}}/schedule",
    response_model=ContentItemPublic,
    summary="approved → scheduled (> now+5min, selected Page of an active connection)",
    responses={
        **_TRANSITION_RESPONSES,
        422: {
            "description": "schedule_too_soon / target_page_not_selected / instagram_not_linked"
        },
    },
)
async def schedule_item(
    brand_id: UUID,
    item_id: UUID,
    payload: ScheduleRequest,
    actor: CurrentUserFresh,
    service: Service,
    resolver: Resolver,
) -> ContentItemPublic:
    """Roles: OWNER, MANAGER. ``scheduled_for`` must be > now + 5 min (422
    ``schedule_too_soon``); the target Page must be selected-with-token in an ``active``
    connection of this brand (422 ``target_page_not_selected``); channel ``instagram`` needs a
    Page with a linked IG account (422 ``instagram_not_linked``). Nothing is published."""
    return await _act(
        service,
        resolver,
        brand_id,
        item_id,
        Action.SCHEDULE,
        actor.tenant_id,
        actor.user_id,
        scheduled_for=payload.scheduled_for,
        target_page_id=payload.target_page_id,
    )


@router.post(
    f"{BRAND_PREFIX}/items/{{item_id}}/unschedule",
    response_model=ContentItemPublic,
    summary="scheduled → approved — OWNER/MANAGER",
    responses=_TRANSITION_RESPONSES,
)
async def unschedule_item(
    brand_id: UUID,
    item_id: UUID,
    actor: CurrentUserFresh,
    service: Service,
    resolver: Resolver,
) -> ContentItemPublic:
    """Roles: OWNER, MANAGER. Clears ``scheduled_for``."""
    return await _act(
        service, resolver, brand_id, item_id, Action.UNSCHEDULE, actor.tenant_id, actor.user_id
    )


@router.post(
    f"{BRAND_PREFIX}/items/{{item_id}}/archive",
    response_model=ContentItemPublic,
    summary="any non-archived → archived (final) — OWNER; MANAGER only own items",
    responses=_TRANSITION_RESPONSES,
)
async def archive_item(
    brand_id: UUID,
    item_id: UUID,
    actor: CurrentUserFresh,
    service: Service,
    resolver: Resolver,
) -> ContentItemPublic:
    """Roles: OWNER (any item), MANAGER (only items it created, else 403
    ``insufficient_role``). ``archived`` is final: every further action ⇒ 409."""
    return await _act(
        service, resolver, brand_id, item_id, Action.ARCHIVE, actor.tenant_id, actor.user_id
    )


@router.post(
    f"{BRAND_PREFIX}/items/{{item_id}}/generate",
    response_model=ContentItemPublic,
    summary="Generate a draft body with the AI gateway (new revision, provenance=ai_generated)",
    responses={
        403: {"description": "insufficient_role"},
        404: {"description": "content_not_found"},
        409: {"description": "invalid_transition — only draft/rejected items can be generated"},
        429: {"description": "generation_rate_limited (20/h per brand) / ai_rate_limited"},
        502: {"description": "ai_upstream_unavailable (fixed body)"},
        503: {"description": "ai_provider_not_configured"},
    },
)
async def generate_item(
    brand_id: UUID,
    item_id: UUID,
    payload: GenerateRequest,
    actor: CurrentUserFresh,
    service: Service,
    resolver: Resolver,
    gateway: Gateway,
    limiter: Limiter,
) -> ContentItemPublic:
    """Roles: OWNER, MANAGER (VIEWER ⇒ 403). State: ``draft`` or ``rejected`` ⇒ afterwards
    ``draft`` with a new revision whose ``provenance`` is
    ``{"kind": "ai_generated", "provider", "model", "prompt_hash", "generated_at", ...}``
    (D-055 evidence). The system prompt is fixed (``prompts.py``) and forbids numeric
    claims absent from the brief. Nothing is published; approval stays with the owner."""
    await _authorise(resolver, actor.tenant_id, brand_id, CONNECTION_WRITE_ROLES)
    try:
        item = await service.generate(
            brand_id,
            item_id,
            gateway=gateway,
            limiter=limiter,
            brief=payload.brief,
            tone=payload.tone,
            language=payload.language,
            actor_tenant_id=actor.tenant_id,
        )
    except ContentError as exc:
        raise to_http(exc) from exc
    except AIRateLimitedError as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error": exc.error, "message": exc.message},
            headers=(
                {"Retry-After": str(exc.retry_after_seconds)}
                if exc.retry_after_seconds is not None
                else None
            ),
        ) from exc
    except AIUpstreamError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail={"error": "ai_upstream_unavailable"}
        ) from exc
    return await _public(service, item)
