"""Protected WhatsApp routes (SPRINT-09 B-2 / B-3) under ``/brands/{brand_id}/whatsapp``.

Roles (§2-0 و): account create/delete ⇒ OWNER; account read ⇒ OWNER/MANAGER (VIEWER 403);
conversations / messages ⇒ every brand role (VIEWER read-only, ``wa_id`` masked, no private
draft provenance); read / send / template / archive / suggest / approve / reject ⇒
OWNER/MANAGER (VIEWER 403). Same policy as ``content``: no access ⇒ 404 ``brand_not_found``;
wrong role ⇒ 403 ``insufficient_role``.
"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.core.database import get_db_session
from src.core.http import HttpClient
from src.core.security import (
    EncryptionKeyMissingError,
    TokenEncryptionService,
    get_token_encryption_service,
)
from src.modules.ai_gateway.dependencies import BudgetReader, Gateway
from src.modules.auth.dependencies import CurrentUser, CurrentUserFresh, get_rate_limiter
from src.modules.auth.domain.rate_limit import RateLimiter
from src.modules.content.exceptions import ContentError
from src.modules.content.router import to_http as content_to_http
from src.modules.tenancy.dependencies import get_brand_access_resolver, get_brand_repository
from src.modules.tenancy.domain.access import (
    CONNECTION_WRITE_ROLES,
    GRANT_ROLES,
    READ_ROLES,
    BrandAccessResolver,
    BrandRole,
)
from src.modules.tenancy.domain.repository import BrandRepository
from src.modules.tenancy.exceptions import BrandNotFoundError, InsufficientRoleError, TenancyError
from src.modules.tenancy.router import to_http as tenancy_to_http
from src.modules.whatsapp.client import WhatsAppCloudClient
from src.modules.whatsapp.domain.models import Conversation, ConversationStatus, Message
from src.modules.whatsapp.domain.repository import Cursor, IndependentWrites, WhatsAppRepository
from src.modules.whatsapp.exceptions import (
    AccountAlreadyExistsError,
    AccountNotFoundError,
    ConversationNotFoundError,
    CredentialsInvalidError,
    DraftNotFoundError,
    DraftNotPendingError,
    DraftPendingError,
    NoInboundToReplyError,
    PermanentSendError,
    PhoneNumberInUseError,
    RateLimitedSendError,
    ReauthRequiredError,
    RecipientRejectedError,
    TransientSendError,
    WhatsAppError,
    WindowClosedError,
)
from src.modules.whatsapp.inbox import WhatsAppInboxService
from src.modules.whatsapp.infrastructure.postgres_repository import (
    PgIndependentWrites,
    PostgresWhatsAppRepository,
)
from src.modules.whatsapp.schemas import (
    ApproveDraftRequest,
    ApproveResultPublic,
    ConversationPage,
    ConversationPublic,
    ConversationStatePublic,
    MessagePublic,
    MessagesPage,
    RejectDraftRequest,
    ReplyDraftPublic,
    SendResultPublic,
    SendTemplateRequest,
    SendTextRequest,
    SuggestReplyRequest,
    WhatsAppAccountCreate,
    WhatsAppAccountPublic,
    WhatsAppAccountRotate,
)
from src.modules.whatsapp.service import PostgresWhatsAppTokenProvider, WhatsAppAccountService
from src.modules.whatsapp.suggestions import ReplySuggestionService

router = APIRouter(tags=["WhatsApp — conversations (M6)"])

BRAND_PREFIX = "/brands/{brand_id}/whatsapp"
MAX_LIST_LIMIT = 100
TRANSIENT_RETRY_AFTER_SECONDS = 30


# ------------------------------------------------------------ dependencies --
def get_whatsapp_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> WhatsAppRepository:
    return PostgresWhatsAppRepository(session)


def get_independent_writes() -> IndependentWrites:
    """SPRINT-10 A-1 (D-072): writes that must survive the request transaction."""
    return PgIndependentWrites()


def get_whatsapp_client(
    settings: Annotated[Settings, Depends(get_settings)], http_client: HttpClient
) -> WhatsAppCloudClient:
    return WhatsAppCloudClient(settings=settings, http_client=http_client)


def get_encryption(settings: Annotated[Settings, Depends(get_settings)]) -> TokenEncryptionService:
    try:
        return get_token_encryption_service(settings)
    except EncryptionKeyMissingError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Token encryption is not configured on the server.",
        ) from exc


Repo = Annotated[WhatsAppRepository, Depends(get_whatsapp_repository)]
Independent = Annotated[IndependentWrites, Depends(get_independent_writes)]
Client = Annotated[WhatsAppCloudClient, Depends(get_whatsapp_client)]
Encryption = Annotated[TokenEncryptionService, Depends(get_encryption)]
Resolver = Annotated[BrandAccessResolver, Depends(get_brand_access_resolver)]
Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]
LimitQuery = Annotated[int, Query(ge=1, le=MAX_LIST_LIMIT)]
CursorQuery = Annotated[str | None, Query(max_length=128)]
IdempotencyKey = Annotated[UUID | None, Header(alias="Idempotency-Key")]


def get_account_service(
    repo: Repo, client: Client, encryption: Encryption
) -> WhatsAppAccountService:
    return WhatsAppAccountService(repo, client, encryption)


def get_inbox_service(
    repo: Repo, client: Client, encryption: Encryption, independent: Independent
) -> WhatsAppInboxService:
    return WhatsAppInboxService(
        repo, client, PostgresWhatsAppTokenProvider(repo, encryption), independent
    )


Accounts = Annotated[WhatsAppAccountService, Depends(get_account_service)]
Inbox = Annotated[WhatsAppInboxService, Depends(get_inbox_service)]
BrandRepo = Annotated[BrandRepository, Depends(get_brand_repository)]


def get_suggestion_service(
    repo: Repo,
    budget_reader: BudgetReader,
    gateway: Gateway,
    inbox: Inbox,
    limiter: Limiter,
    independent: Independent,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ReplySuggestionService:
    return ReplySuggestionService(
        repo,
        budget_reader,
        gateway,
        inbox,
        limiter,
        independent,
        daily_token_budget=settings.ai_daily_token_budget_per_brand,
    )


Suggestions = Annotated[ReplySuggestionService, Depends(get_suggestion_service)]


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


def to_http(exc: WhatsAppError) -> HTTPException:
    """Service error ⇒ HTTP. Never carries Meta's message text (codes only)."""
    detail: dict[str, object] = {"error": exc.error}
    headers: dict[str, str] | None = None
    if isinstance(exc, AccountNotFoundError | ConversationNotFoundError):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, AccountAlreadyExistsError | PhoneNumberInUseError):
        code = status.HTTP_409_CONFLICT
    elif isinstance(exc, CredentialsInvalidError):
        code = status.HTTP_422_UNPROCESSABLE_CONTENT
    elif isinstance(exc, WindowClosedError):
        code = status.HTTP_422_UNPROCESSABLE_CONTENT
        detail["template_required"] = True
        if exc.draft_status is not None:  # A-1: the decision that was persisted
            detail["draft_status"] = exc.draft_status
    elif isinstance(exc, RecipientRejectedError):
        code = status.HTTP_422_UNPROCESSABLE_CONTENT
        detail["code"] = exc.code
    elif isinstance(exc, TransientSendError):
        code = status.HTTP_503_SERVICE_UNAVAILABLE
        headers = {"Retry-After": str(TRANSIENT_RETRY_AFTER_SECONDS)}
    elif isinstance(exc, RateLimitedSendError):
        code = status.HTTP_429_TOO_MANY_REQUESTS
        if exc.retry_after_seconds is not None:
            headers = {"Retry-After": str(int(exc.retry_after_seconds))}
    elif isinstance(exc, ReauthRequiredError):
        code = status.HTTP_409_CONFLICT
        detail["required_action"] = "reauth"
    elif isinstance(exc, PermanentSendError):
        code = status.HTTP_422_UNPROCESSABLE_CONTENT
        detail["code"] = exc.code
    elif isinstance(exc, NoInboundToReplyError | DraftPendingError):
        code = status.HTTP_409_CONFLICT
    elif isinstance(exc, DraftNotFoundError):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, DraftNotPendingError):
        code = status.HTTP_409_CONFLICT
        detail["current_status"] = exc.current_status
    else:
        code = status.HTTP_400_BAD_REQUEST
    return HTTPException(code, detail=detail, headers=headers)


def encode_cursor(at: datetime | None, item_id: UUID) -> str:
    raw = f"{(at.isoformat() if at is not None else '')}|{item_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> Cursor | None:
    if cursor is None:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        at, item_id = base64.urlsafe_b64decode(padded).decode().split("|", 1)
        return datetime.fromisoformat(at), UUID(item_id)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "invalid_cursor", "message": "cursor is not valid"},
        ) from exc


# ------------------------------------------------------------------- account ---
@router.post(
    f"{BRAND_PREFIX}/account",
    response_model=WhatsAppAccountPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Connect the brand's Cloud API number (BYOK) — OWNER",
    responses={
        200: {"description": "A-2: the brand's disabled account was reconnected in place"},
        409: {"description": "whatsapp_account_exists / whatsapp_phone_number_in_use"},
    },
)
async def connect_account(
    brand_id: UUID,
    payload: WhatsAppAccountCreate,
    actor: CurrentUserFresh,
    resolver: Resolver,
    service: Accounts,
    response: Response,
) -> WhatsAppAccountPublic:
    await _authorise(resolver, actor.tenant_id, brand_id, GRANT_ROLES)
    try:
        outcome = await service.connect_account(
            brand_id,
            actor.user_id,
            phone_number_id=payload.phone_number_id,
            waba_id=payload.waba_id,
            display_phone=payload.display_phone,
            system_token=payload.system_token,
            app_secret=payload.app_secret,
            verify_token=payload.verify_token,
        )
    except WhatsAppError as exc:
        raise to_http(exc) from exc
    if outcome.reconnected:
        response.status_code = status.HTTP_200_OK
    return WhatsAppAccountPublic.from_domain(outcome.account)


@router.post(
    f"{BRAND_PREFIX}/account/rotate",
    response_model=WhatsAppAccountPublic,
    summary="Rotate system token / app secret of the active account — OWNER",
    responses={422: {"description": "whatsapp_credentials_invalid — nothing changed"}},
)
async def rotate_account_credentials(
    brand_id: UUID,
    payload: WhatsAppAccountRotate,
    actor: CurrentUserFresh,
    resolver: Resolver,
    service: Accounts,
) -> WhatsAppAccountPublic:
    await _authorise(resolver, actor.tenant_id, brand_id, GRANT_ROLES)
    try:
        account = await service.rotate_credentials(
            brand_id, system_token=payload.system_token, app_secret=payload.app_secret
        )
    except WhatsAppError as exc:
        raise to_http(exc) from exc
    return WhatsAppAccountPublic.from_domain(account)


@router.get(
    f"{BRAND_PREFIX}/account",
    response_model=WhatsAppAccountPublic,
    summary="The brand's WhatsApp account — OWNER/MANAGER",
)
async def get_account(
    brand_id: UUID, actor: CurrentUser, resolver: Resolver, service: Accounts
) -> WhatsAppAccountPublic:
    await _authorise(resolver, actor.tenant_id, brand_id, CONNECTION_WRITE_ROLES)
    try:
        return WhatsAppAccountPublic.from_domain(await service.get_account(brand_id))
    except WhatsAppError as exc:
        raise to_http(exc) from exc


@router.delete(
    f"{BRAND_PREFIX}/account",
    response_model=WhatsAppAccountPublic,
    summary="Disable the brand's WhatsApp account — OWNER",
)
async def disable_account(
    brand_id: UUID, actor: CurrentUserFresh, resolver: Resolver, service: Accounts
) -> WhatsAppAccountPublic:
    await _authorise(resolver, actor.tenant_id, brand_id, GRANT_ROLES)
    try:
        return WhatsAppAccountPublic.from_domain(await service.disable_account(brand_id))
    except WhatsAppError as exc:
        raise to_http(exc) from exc


# ------------------------------------------------------------- conversations ---
@router.get(
    f"{BRAND_PREFIX}/conversations",
    response_model=ConversationPage,
    summary="Unified inbox — all roles (VIEWER: wa_id masked)",
)
async def list_conversations(
    brand_id: UUID,
    actor: CurrentUser,
    resolver: Resolver,
    inbox: Inbox,
    status_: Annotated[ConversationStatus | None, Query(alias="status")] = ConversationStatus.OPEN,
    cursor: CursorQuery = None,
    limit: LimitQuery = 20,
) -> ConversationPage:
    role = await _authorise(resolver, actor.tenant_id, brand_id, READ_ROLES)
    rows = await inbox.list_conversations(
        brand_id, status=status_, limit=limit, cursor=decode_cursor(cursor)
    )
    page, rest = rows[:limit], rows[limit:]
    mask = role is BrandRole.VIEWER
    items = [
        ConversationPublic.from_domain(
            r.conversation,
            preview=r.last_message_preview,
            pending_draft=r.pending_draft,
            window_open=r.window_open,
            mask=mask,
        )
        for r in page
    ]
    next_cursor = (
        encode_cursor(page[-1].conversation.last_inbound_at, page[-1].conversation.id)
        if rest and page
        else None
    )
    return ConversationPage(items=items, next_cursor=next_cursor)


@router.get(
    f"{BRAND_PREFIX}/conversations/{{conversation_id}}/messages",
    response_model=MessagesPage,
    summary="Messages (ascending) + reply drafts — all roles (VIEWER: no private provenance)",
)
async def list_messages(
    brand_id: UUID,
    conversation_id: UUID,
    actor: CurrentUser,
    resolver: Resolver,
    inbox: Inbox,
    cursor: CursorQuery = None,
    limit: LimitQuery = 50,
) -> MessagesPage:
    role = await _authorise(resolver, actor.tenant_id, brand_id, READ_ROLES)
    try:
        conv, messages, drafts = await inbox.list_messages(
            brand_id, conversation_id, limit=limit, cursor=decode_cursor(cursor)
        )
    except WhatsAppError as exc:
        raise to_http(exc) from exc
    page, rest = messages[:limit], messages[limit:]
    private = role is not BrandRole.VIEWER
    return MessagesPage(
        conversation=ConversationStatePublic.from_domain(conv),
        items=[MessagePublic.from_domain(m, private=private) for m in page],
        reply_drafts=[ReplyDraftPublic.from_domain(d, private=private) for d in drafts],
        next_cursor=encode_cursor(page[-1].created_at, page[-1].id) if rest and page else None,
    )


@router.post(
    f"{BRAND_PREFIX}/conversations/{{conversation_id}}/read",
    response_model=ConversationStatePublic,
    summary="Mark read (unread_count = 0, best-effort read receipt) — OWNER/MANAGER",
)
async def mark_read(
    brand_id: UUID, conversation_id: UUID, actor: CurrentUser, resolver: Resolver, inbox: Inbox
) -> ConversationStatePublic:
    await _authorise(resolver, actor.tenant_id, brand_id, CONNECTION_WRITE_ROLES)
    try:
        conv = await inbox.mark_read(brand_id, conversation_id)
        return ConversationStatePublic.from_domain(conv)
    except WhatsAppError as exc:
        raise to_http(exc) from exc


@router.post(
    f"{BRAND_PREFIX}/conversations/{{conversation_id}}/archive",
    response_model=ConversationStatePublic,
    summary="Archive the conversation (reopens on the next inbound) — OWNER/MANAGER",
)
async def archive_conversation(
    brand_id: UUID, conversation_id: UUID, actor: CurrentUser, resolver: Resolver, inbox: Inbox
) -> ConversationStatePublic:
    await _authorise(resolver, actor.tenant_id, brand_id, CONNECTION_WRITE_ROLES)
    try:
        return ConversationStatePublic.from_domain(await inbox.archive(brand_id, conversation_id))
    except WhatsAppError as exc:
        raise to_http(exc) from exc


def _send_response(result_message: Message, *, duplicate: bool) -> SendResultPublic:
    # Send routes are OWNER/MANAGER only ⇒ the private view (wamid) is always allowed.
    return SendResultPublic(
        message=MessagePublic.from_domain(result_message, private=True),
        delivery_status=result_message.delivery_status,
        duplicate=duplicate,
    )


async def _handle_send_error(exc: WhatsAppError, inbox: Inbox, conv: Conversation | None) -> None:
    if isinstance(exc, ReauthRequiredError) and conv is not None:
        await inbox.disable_account_after_auth_failure(conv)
    raise to_http(exc) from exc


@router.post(
    f"{BRAND_PREFIX}/conversations/{{conversation_id}}/messages",
    response_model=SendResultPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Send free-form text inside the 24h window — OWNER/MANAGER (human actor)",
    responses={
        200: {"description": "Idempotency-Key replay — the existing message, no second send"},
        202: {"description": "delivery_status=ambiguous — sent once, outcome unknown (D-070)"},
        422: {"description": "whatsapp_window_closed (template_required) / recipient rejected"},
        429: {"description": "whatsapp_rate_limited"},
        503: {"description": "whatsapp_transient — safe to retry after Retry-After"},
    },
)
async def send_text(
    brand_id: UUID,
    conversation_id: UUID,
    payload: SendTextRequest,
    actor: CurrentUserFresh,
    resolver: Resolver,
    inbox: Inbox,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> SendResultPublic:
    await _authorise(resolver, actor.tenant_id, brand_id, CONNECTION_WRITE_ROLES)
    conv: Conversation | None = None
    try:
        conv = await inbox.get_conversation(brand_id, conversation_id)
        result = await inbox.send_text(
            brand_id,
            conversation_id,
            body=payload.body,
            actor_user_id=actor.user_id,
            client_message_id=idempotency_key,
        )
    except WhatsAppError as exc:
        await _handle_send_error(exc, inbox, conv)
        raise  # pragma: no cover - _handle_send_error always raises
    if result.duplicate:
        response.status_code = status.HTTP_200_OK
    elif result.message.delivery_status.value == "ambiguous":
        response.status_code = status.HTTP_202_ACCEPTED
    return _send_response(result.message, duplicate=result.duplicate)


@router.post(
    f"{BRAND_PREFIX}/conversations/{{conversation_id}}/template",
    response_model=SendResultPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Send an approved template (inside or outside the window) — OWNER/MANAGER",
)
async def send_template(
    brand_id: UUID,
    conversation_id: UUID,
    payload: SendTemplateRequest,
    actor: CurrentUserFresh,
    resolver: Resolver,
    inbox: Inbox,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> SendResultPublic:
    await _authorise(resolver, actor.tenant_id, brand_id, CONNECTION_WRITE_ROLES)
    conv: Conversation | None = None
    try:
        conv = await inbox.get_conversation(brand_id, conversation_id)
        result = await inbox.send_template(
            brand_id,
            conversation_id,
            name=payload.name,
            language_code=payload.language_code,
            components=payload.components,
            actor_user_id=actor.user_id,
            client_message_id=idempotency_key,
        )
    except WhatsAppError as exc:
        await _handle_send_error(exc, inbox, conv)
        raise  # pragma: no cover
    if result.duplicate:
        response.status_code = status.HTTP_200_OK
    elif result.message.delivery_status.value == "ambiguous":
        response.status_code = status.HTTP_202_ACCEPTED
    return _send_response(result.message, duplicate=result.duplicate)


# -------------------------------------------------------------- reply drafts (B-3) ---
@router.post(
    f"{BRAND_PREFIX}/conversations/{{conversation_id}}/suggest-reply",
    response_model=ReplyDraftPublic,
    status_code=status.HTTP_201_CREATED,
    summary="AI reply suggestion (draft only, never sent) — OWNER/MANAGER",
    responses={
        409: {"description": "no_inbound_to_reply / draft_pending"},
        429: {"description": "generation_rate_limited / generation_budget_exceeded (A-5)"},
    },
)
async def suggest_reply(
    brand_id: UUID,
    conversation_id: UUID,
    actor: CurrentUser,
    resolver: Resolver,
    suggestions: Suggestions,
    brands: BrandRepo,
    payload: SuggestReplyRequest | None = None,
) -> ReplyDraftPublic:
    await _authorise(resolver, actor.tenant_id, brand_id, CONNECTION_WRITE_ROLES)
    payload = payload or SuggestReplyRequest()
    brand = await brands.get(brand_id)
    try:
        draft = await suggestions.suggest(
            brand_id,
            conversation_id,
            actor_tenant_id=actor.tenant_id,
            brand_display_name=brand.name if brand is not None else "",
            tone=payload.tone,
            language=payload.language,
        )
    except WhatsAppError as exc:
        raise to_http(exc) from exc
    except ContentError as exc:  # rate limit / budget — same codes as SPRINT-07 A-5
        raise content_to_http(exc) from exc
    return ReplyDraftPublic.from_domain(draft, private=True)


@router.post(
    f"{BRAND_PREFIX}/reply-drafts/{{draft_id}}/approve",
    response_model=ApproveResultPublic,
    summary="Approve (optionally edit) a suggested reply, then send it — OWNER/MANAGER",
    responses={
        202: {"description": "sent once, outcome ambiguous (D-070)"},
        409: {"description": "draft_not_pending"},
        422: {
            "description": "whatsapp_window_closed + draft_status — the decision is persisted "
            "(D-072), nothing sent"
        },
    },
)
async def approve_reply_draft(
    brand_id: UUID,
    draft_id: UUID,
    actor: CurrentUserFresh,
    resolver: Resolver,
    suggestions: Suggestions,
    inbox: Inbox,
    response: Response,
    payload: ApproveDraftRequest | None = None,
) -> ApproveResultPublic:
    await _authorise(resolver, actor.tenant_id, brand_id, CONNECTION_WRITE_ROLES)
    payload = payload or ApproveDraftRequest()
    conv: Conversation | None = None
    try:
        draft = await suggestions.get_pending_or_raise(brand_id, draft_id)
        conv = await inbox.get_conversation(brand_id, draft.conversation_id)
        outcome = await suggestions.approve(
            brand_id, draft_id, actor_user_id=actor.user_id, final_body=payload.final_body
        )
    except WhatsAppError as exc:
        await _handle_send_error(exc, inbox, conv)
        raise  # pragma: no cover - _handle_send_error always raises
    message = outcome.message
    if message is not None and message.delivery_status.value == "ambiguous":
        response.status_code = status.HTTP_202_ACCEPTED
    return ApproveResultPublic(
        draft=ReplyDraftPublic.from_domain(outcome.draft, private=True),
        message=MessagePublic.from_domain(message, private=True) if message is not None else None,
        delivery_status=message.delivery_status if message is not None else None,
    )


@router.post(
    f"{BRAND_PREFIX}/reply-drafts/{{draft_id}}/reject",
    response_model=ReplyDraftPublic,
    summary="Reject a suggested reply (learning signal only) — OWNER/MANAGER",
    responses={409: {"description": "draft_not_pending"}},
)
async def reject_reply_draft(
    brand_id: UUID,
    draft_id: UUID,
    payload: RejectDraftRequest,
    actor: CurrentUser,
    resolver: Resolver,
    suggestions: Suggestions,
) -> ReplyDraftPublic:
    await _authorise(resolver, actor.tenant_id, brand_id, CONNECTION_WRITE_ROLES)
    try:
        draft = await suggestions.reject(
            brand_id, draft_id, actor_user_id=actor.user_id, reason=payload.reason
        )
    except WhatsAppError as exc:
        raise to_http(exc) from exc
    return ReplyDraftPublic.from_domain(draft, private=True)
