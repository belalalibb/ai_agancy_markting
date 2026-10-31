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
from src.modules.auth.dependencies import CurrentUser, CurrentUserFresh, get_rate_limiter
from src.modules.auth.domain.rate_limit import RateLimiter
from src.modules.tenancy.dependencies import get_brand_access_resolver
from src.modules.tenancy.domain.access import (
    CONNECTION_WRITE_ROLES,
    GRANT_ROLES,
    READ_ROLES,
    BrandAccessResolver,
    BrandRole,
)
from src.modules.tenancy.exceptions import BrandNotFoundError, InsufficientRoleError, TenancyError
from src.modules.tenancy.router import to_http as tenancy_to_http
from src.modules.whatsapp.client import WhatsAppCloudClient
from src.modules.whatsapp.domain.models import Conversation, ConversationStatus, Message
from src.modules.whatsapp.domain.repository import Cursor, WhatsAppRepository
from src.modules.whatsapp.exceptions import (
    AccountAlreadyExistsError,
    AccountNotFoundError,
    ConversationNotFoundError,
    CredentialsInvalidError,
    PermanentSendError,
    RateLimitedSendError,
    ReauthRequiredError,
    RecipientRejectedError,
    TransientSendError,
    WhatsAppError,
    WindowClosedError,
)
from src.modules.whatsapp.inbox import WhatsAppInboxService
from src.modules.whatsapp.infrastructure.postgres_repository import PostgresWhatsAppRepository
from src.modules.whatsapp.schemas import (
    ConversationPage,
    ConversationPublic,
    ConversationStatePublic,
    MessagePublic,
    MessagesPage,
    ReplyDraftPublic,
    SendResultPublic,
    SendTemplateRequest,
    SendTextRequest,
    WhatsAppAccountCreate,
    WhatsAppAccountPublic,
)
from src.modules.whatsapp.service import PostgresWhatsAppTokenProvider, WhatsAppAccountService

router = APIRouter(tags=["WhatsApp — conversations (M6)"])

BRAND_PREFIX = "/brands/{brand_id}/whatsapp"
MAX_LIST_LIMIT = 100
TRANSIENT_RETRY_AFTER_SECONDS = 30


# ------------------------------------------------------------ dependencies --
def get_whatsapp_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> WhatsAppRepository:
    return PostgresWhatsAppRepository(session)


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


def get_inbox_service(repo: Repo, client: Client, encryption: Encryption) -> WhatsAppInboxService:
    return WhatsAppInboxService(repo, client, PostgresWhatsAppTokenProvider(repo, encryption))


Accounts = Annotated[WhatsAppAccountService, Depends(get_account_service)]
Inbox = Annotated[WhatsAppInboxService, Depends(get_inbox_service)]


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
    elif isinstance(exc, AccountAlreadyExistsError):
        code = status.HTTP_409_CONFLICT
    elif isinstance(exc, CredentialsInvalidError):
        code = status.HTTP_422_UNPROCESSABLE_CONTENT
    elif isinstance(exc, WindowClosedError):
        code = status.HTTP_422_UNPROCESSABLE_CONTENT
        detail["template_required"] = True
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
)
async def connect_account(
    brand_id: UUID,
    payload: WhatsAppAccountCreate,
    actor: CurrentUserFresh,
    resolver: Resolver,
    service: Accounts,
) -> WhatsAppAccountPublic:
    await _authorise(resolver, actor.tenant_id, brand_id, GRANT_ROLES)
    try:
        account = await service.connect_account(
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
        items=[MessagePublic.from_domain(m) for m in page],
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
    return SendResultPublic(
        message=MessagePublic.from_domain(result_message),
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
