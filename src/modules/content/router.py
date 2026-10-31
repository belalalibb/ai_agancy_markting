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

from src.core.config import Settings, get_settings
from src.core.database import get_db_session
from src.modules.ai_gateway.contract import AIError, ErrorCategory
from src.modules.ai_gateway.dependencies import Gateway
from src.modules.auth.dependencies import CurrentUser, CurrentUserFresh, get_rate_limiter
from src.modules.auth.domain.rate_limit import RateLimiter
from src.modules.content.domain.lifecycle import Action
from src.modules.content.domain.models import Channel, ContentItem, ContentStatus
from src.modules.content.domain.repository import ContentRepository
from src.modules.content.exceptions import (
    BodyTooLongError,
    ContentError,
    GenerationBudgetExceededError,
    GenerationRateLimitedError,
    InvalidTransitionError,
    ReauthRequiredError,
)
from src.modules.content.infrastructure.postgres_repository import PostgresContentRepository
from src.modules.content.schemas import (
    ApprovalEventPublic,
    ApprovalNoteRequest,
    ContentBodyUpdate,
    ContentItemCreate,
    ContentItemDetail,
    ContentItemDetailWithUsage,
    ContentItemPage,
    ContentItemPublic,
    ContentRevisionPublic,
    GenerateRequest,
    GenerationLogEntry,
    PublishStatusDetail,
    PublishStatusPublic,
    RejectRequest,
    ScheduleRequest,
    StatusLiteral,
)
from src.modules.content.service import ContentService
from src.modules.publishing.domain.repository import PublishJobRepository
from src.modules.publishing.infrastructure.postgres_repository import (
    PostgresPublishJobRepository,
)
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


def get_publish_job_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PublishJobRepository:
    """B2: same request session as the content repository ⇒ CAS + job insert are one
    transaction."""
    return PostgresPublishJobRepository(session)


def get_content_service(
    repository: Annotated[ContentRepository, Depends(get_content_repository)],
    connections: Annotated[ConnectedAccountRepository, Depends(get_connection_repository)],
    publish_jobs: Annotated[PublishJobRepository, Depends(get_publish_job_repository)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ContentService:
    return ContentService(
        repository,
        connections,
        publish_jobs,
        daily_token_budget=settings.ai_daily_token_budget_per_brand,
        publish_max_attempts=settings.publish_max_attempts,  # SPRINT-08 A-3
    )


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
    headers: dict[str, str] | None = None
    if isinstance(exc, InvalidTransitionError):
        detail["from"] = exc.from_status.value
        detail["action"] = exc.action
    elif isinstance(exc, BodyTooLongError):  # A-2
        detail.update(channel=exc.channel.value, limit=exc.limit, length=exc.length)
    elif isinstance(exc, GenerationBudgetExceededError):  # A-5
        detail.update(budget=exc.budget, used=exc.used, resets_in_seconds=exc.resets_in_seconds)
        headers = {"Retry-After": str(exc.resets_in_seconds)}
    elif isinstance(exc, GenerationRateLimitedError):
        headers = {"Retry-After": str(exc.retry_after_seconds)}
    elif isinstance(exc, ReauthRequiredError):  # SPRINT-07 B2
        detail.update(required_action=exc.required_action, missing_scopes=exc.missing_scopes)
    return HTTPException(exc.http_status, detail=detail, headers=headers)


Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]

# B-0 (D-067): the single HTTP map for the twelve provider error categories. Anything not
# listed is a fixed 502 ``ai_upstream_unavailable`` — no provider text ever reaches clients.
_AI_HTTP: dict[ErrorCategory, tuple[int, str]] = {
    ErrorCategory.RATE_LIMITED: (status.HTTP_429_TOO_MANY_REQUESTS, "ai_rate_limited"),
    ErrorCategory.QUOTA_EXCEEDED: (status.HTTP_429_TOO_MANY_REQUESTS, "ai_quota_exceeded"),
    ErrorCategory.INVALID_CREDENTIAL: (
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "ai_provider_misconfigured",
    ),
    ErrorCategory.AUTH_EXPIRED: (status.HTTP_503_SERVICE_UNAVAILABLE, "ai_provider_misconfigured"),
    ErrorCategory.CONTENT_REJECTED: (
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "ai_content_rejected",
    ),
}
_AI_HTTP_DEFAULT = (status.HTTP_502_BAD_GATEWAY, "ai_upstream_unavailable")


def _ai_error_to_http(exc: AIError) -> HTTPException:
    http_status, error = _AI_HTTP.get(exc.category, _AI_HTTP_DEFAULT)
    headers = None
    if exc.category is ErrorCategory.RATE_LIMITED and exc.retry_after_ms is not None:
        headers = {"Retry-After": str(max(1, -(-exc.retry_after_ms // 1000)))}
    return HTTPException(
        http_status, detail={"error": error, "category": exc.category.value}, headers=headers
    )


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


# ------------------------------------------------------------------- routes --
@router.post(
    f"{BRAND_PREFIX}/items",
    response_model=ContentItemPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a draft (revision 1, provenance=human) — OWNER/MANAGER",
    responses={
        403: {"description": "insufficient_role"},
        404: {"description": "brand_not_found"},
        422: {"description": "body_too_long {channel, limit, length}"},
    },
)
async def create_item(
    brand_id: UUID,
    payload: ContentItemCreate,
    actor: CurrentUserFresh,
    service: Service,
    resolver: Resolver,
) -> ContentItemPublic:
    """Roles: OWNER, MANAGER (VIEWER ⇒ 403 ``insufficient_role``). Result: ``draft``.
    Body longer than the channel limit (Instagram 2200 / Facebook 5000) ⇒ 422
    ``body_too_long`` (A-2)."""
    await _authorise(resolver, actor.tenant_id, brand_id, CONNECTION_WRITE_ROLES)
    try:
        item = await service.create_item(
            brand_id,
            actor_tenant_id=actor.tenant_id,
            channel=Channel(payload.channel),
            body=payload.body,
            target_page_id=payload.target_page_id,
            media_refs=[ref.model_dump() for ref in payload.media_refs],
        )
    except ContentError as exc:
        raise to_http(exc) from exc
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
        items=[ContentItemPublic.from_domain(item, current_body=body) for item, body in items],
        next_cursor=_encode_cursor(items[-1][0]) if has_more and items else None,
        has_more=has_more,
    )


@router.get(
    f"{BRAND_PREFIX}/items/{{item_id}}",
    response_model=ContentItemDetail | ContentItemDetailWithUsage,
    summary="Item detail with last 20 revisions and the approval log — all roles",
    responses={404: {"description": "content_not_found / brand_not_found"}},
)
async def get_item(
    brand_id: UUID, item_id: UUID, actor: CurrentUser, service: Service, resolver: Resolver
) -> ContentItemDetail | ContentItemDetailWithUsage:
    """Roles: OWNER, MANAGER, VIEWER. Two response models (A-4): OWNER/MANAGER receive
    ``ContentItemDetailWithUsage`` (``generation_log`` with provider/model/token usage of the
    AI revisions); VIEWER receives ``ContentItemDetail`` — the key is absent, not ``null``
    (§2-0 b). ``revisions[].provenance`` is the public projection for every role (A-1)."""
    role = await _authorise(resolver, actor.tenant_id, brand_id, READ_ROLES)
    try:
        item, revisions, events = await service.get_detail(brand_id, item_id)
    except ContentError as exc:
        raise to_http(exc) from exc
    base = ContentItemDetail(
        **ContentItemPublic.from_domain(
            item, current_body=revisions[0].body if revisions else None
        ).model_dump(),
        revisions=[ContentRevisionPublic.from_domain(r) for r in revisions],
        approval_events=[ApprovalEventPublic.from_domain(e) for e in events],
    )
    if role is BrandRole.VIEWER:
        return base
    return ContentItemDetailWithUsage(
        **base.model_dump(),
        generation_log=[
            GenerationLogEntry.from_revision(r)
            for r in revisions
            if r.provenance.get("kind") == "ai_generated"
        ],
    )


@router.put(
    f"{BRAND_PREFIX}/items/{{item_id}}/body",
    response_model=ContentItemPublic,
    summary="New revision of the body — OWNER/MANAGER",
    responses={
        403: {"description": "insufficient_role"},
        404: {"description": "content_not_found"},
        409: {"description": "invalid_transition (archived items are immutable)"},
        422: {"description": "body_too_long {channel, limit, length}"},
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
            media_refs=[ref.model_dump() for ref in payload.media_refs],
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
    return ContentItemPublic.from_domain(item, current_body=await _body_of(service, item))


async def _body_of(service: ContentService, item: ContentItem) -> str | None:
    revisions = await service.list_revisions(item.id, limit=1)
    return revisions[0].body if revisions else None


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


# ------------------------------------------------------ SPRINT-07 B7 publish status ---
@router.get(
    f"{BRAND_PREFIX}/items/{{item_id}}/publish",
    response_model=PublishStatusPublic | PublishStatusDetail,
    summary="Publish status: current/latest job + attempts — all roles (VIEWER: reduced)",
    responses={404: {"description": "content_not_found / no_publish_job / brand_not_found"}},
)
async def get_publish_status(
    brand_id: UUID, item_id: UUID, actor: CurrentUser, service: Service, resolver: Resolver
) -> PublishStatusPublic | PublishStatusDetail:
    """Roles: OWNER, MANAGER, VIEWER. Two response models (A-4): OWNER/MANAGER receive
    ``PublishStatusDetail`` (error classes, Graph codes/trace ids, external ids, lease, who
    authorised); VIEWER receives ``PublishStatusPublic`` — attempts carry only
    ``attempt_no/started_at/finished_at/outcome``. 404 ``no_publish_job`` if never scheduled."""
    role = await _authorise(resolver, actor.tenant_id, brand_id, READ_ROLES)
    try:
        job, attempts = await service.publish_status(brand_id, item_id)
    except ContentError as exc:
        raise to_http(exc) from exc
    if role is BrandRole.VIEWER:
        return PublishStatusPublic.from_domain(job, attempts)
    return PublishStatusDetail.from_domain(job, attempts)


@router.post(
    f"{BRAND_PREFIX}/items/{{item_id}}/publish/retry",
    response_model=ContentItemPublic,
    summary="publish_failed → scheduled (new queued job, run_at=now) — OWNER/MANAGER",
    responses={
        **_TRANSITION_RESPONSES,
        409: {
            "description": "invalid_transition / manual_verification_required (ambiguous) / "
            "reauth_required / publish_in_progress"
        },
        422: {"description": "target_page_not_selected / instagram_requires_image"},
    },
)
async def retry_publish(
    brand_id: UUID,
    item_id: UUID,
    actor: CurrentUserFresh,
    service: Service,
    resolver: Resolver,
) -> ContentItemPublic:
    """Roles: OWNER, MANAGER. Only for ``publish_failed`` items whose last outcome was **not**
    ambiguous (else 409 ``manual_verification_required`` — the post may already be live).
    Re-validates the target like ``schedule`` and records a new ``scheduled`` event."""
    role = await _authorise(resolver, actor.tenant_id, brand_id, CONNECTION_WRITE_ROLES)
    try:
        item = await service.retry_publish(
            brand_id,
            item_id,
            actor_role=role,
            actor_tenant_id=actor.tenant_id,
            actor_user_id=actor.user_id,
        )
    except ContentError as exc:
        raise to_http(exc) from exc
    return ContentItemPublic.from_domain(item, current_body=await _body_of(service, item))


@router.post(
    f"{BRAND_PREFIX}/items/{{item_id}}/generate",
    response_model=ContentItemPublic,
    summary="Generate a draft body with the AI gateway (new revision, provenance=ai_generated)",
    responses={
        403: {"description": "insufficient_role"},
        404: {"description": "content_not_found"},
        409: {"description": "invalid_transition — only draft/rejected items can be generated"},
        422: {"description": "ai_content_rejected"},
        429: {
            "description": "generation_rate_limited (20/h per brand) / "
            "generation_budget_exceeded (daily tokens) / ai_rate_limited / ai_quota_exceeded"
        },
        502: {"description": "ai_upstream_unavailable (fixed body; every other category)"},
        503: {"description": "ai_provider_not_configured / ai_provider_misconfigured"},
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
    except AIError as exc:
        raise _ai_error_to_http(exc) from exc
    return ContentItemPublic.from_domain(item, current_body=await _body_of(service, item))
