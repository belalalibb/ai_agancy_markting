"""HTTP routes for the Meta connector — brand-scoped (SPRINT-03 B9).

Every brand-scoped route requires ``Authorization: Bearer <access JWT>``; the acting tenant
is the token's ``tid`` claim (SPRINT-04 B1/B2). Destructive routes (selection, disconnect)
use ``CurrentUserFresh`` — a logged-out session is refused immediately.

| Method | Path (under /brands/{brand_id}/social/meta)   | Identity              |
|--------|-----------------------------------------------|-----------------------|
| POST   | …/connections/start                           | Bearer, OWNER/MANAGER |
| GET    | /social/meta/callback?code&state (top-level)  | signed ``state`` only |
| GET    | …/connections                                 | Bearer, any role      |
| GET    | …/connections/{id}                            | Bearer, any role      |
| PUT    | …/connections/{id}/pages/selection            | fresh, connection owner |
| DELETE | …/connections/{id}                            | fresh, OWNER or conn. owner |

H1 (SPRINT-04.1): unselected Pages are shown only to the connection owner
(``ConnectedAccountPublic.from_domain(..., viewer_tenant_id=actor.tenant_id)``).

The callback is **unauthenticated by design** (B3): Meta redirects the browser there without
our Bearer token, so ``(tenant_id, brand_id)`` come from the HMAC-signed ``state`` issued to
an authenticated user (``u`` claim = who started the flow, audit only) and are authorised
**before any Graph call** — a revoked grant yields 404/403 with no Graph request and no row.

No response ever carries a token — raw, encrypted or masked (Zero-Secret).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.core.database import get_db_session
from src.core.oauth_state import (
    InvalidOAuthStateError,
    OAuthStateSecretMissingError,
    OAuthStateSigner,
    get_oauth_state_signer,
)
from src.core.security import (
    EncryptionKeyMissingError,
    TokenEncryptionService,
    get_token_encryption_service,
)
from src.modules.auth.dependencies import CurrentUser, CurrentUserFresh
from src.modules.social_connect.domain.repository import ConnectedAccountRepository
from src.modules.social_connect.domain.status_marker import StatusMarker
from src.modules.social_connect.exceptions import (
    MetaGraphError,
    MetaNetworkError,
    MetaOAuthError,
    MetaPermissionError,
    MetaRateLimitError,
    MetaTokenExpiredError,
    MetaTransientError,
    NotConnectionOwnerError,
    PageNotSelectableError,
    UnknownPageIdsError,
)
from src.modules.social_connect.infrastructure.postgres_repository import (
    PostgresConnectedAccountRepository,
)
from src.modules.social_connect.infrastructure.status_marker import PostgresStatusMarker
from src.modules.social_connect.meta_client import MetaGraphClient
from src.modules.social_connect.schemas import (
    ConnectedAccountPublic,
    ConnectionStartRequest,
    MetaAuthorizeUrlResponse,
    PageSelectionRequest,
)
from src.modules.social_connect.service import MetaConnectService
from src.modules.tenancy.dependencies import ActorTenantId, get_brand_access_resolver
from src.modules.tenancy.domain.access import (
    CONNECTION_WRITE_ROLES,
    READ_ROLES,
    BrandAccessResolver,
    BrandRole,
)
from src.modules.tenancy.exceptions import BrandNotFoundError, InsufficientRoleError, TenancyError
from src.modules.tenancy.router import to_http as tenancy_to_http

router = APIRouter(tags=["Social Connect — Meta"])

BRAND_PREFIX = "/brands/{brand_id}/social/meta"


# ------------------------------------------------------------ dependencies --
def get_http_client(request: Request) -> httpx.AsyncClient | None:
    """Shared ``httpx.AsyncClient`` created in the app lifespan (``None`` → client owns one)."""
    return getattr(request.app.state, "http_client", None)


def get_meta_client(
    settings: Annotated[Settings, Depends(get_settings)],
    http_client: Annotated[httpx.AsyncClient | None, Depends(get_http_client)],
) -> MetaGraphClient:
    return MetaGraphClient(settings=settings, http_client=http_client)


def get_encryption_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> TokenEncryptionService:
    try:
        return get_token_encryption_service(settings)
    except EncryptionKeyMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Token encryption is not configured on the server.",
        ) from exc


def get_state_signer(settings: Annotated[Settings, Depends(get_settings)]) -> OAuthStateSigner:
    try:
        return get_oauth_state_signer(settings)
    except OAuthStateSecretMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OAuth state signing is not configured on the server.",
        ) from exc


def get_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ConnectedAccountRepository:
    return PostgresConnectedAccountRepository(session)


def get_status_marker() -> StatusMarker:
    """H3: writes ``reauth_required`` in its own transaction (tests override with a fake)."""
    return PostgresStatusMarker()


def get_meta_service(
    client: Annotated[MetaGraphClient, Depends(get_meta_client)],
    encryption: Annotated[TokenEncryptionService, Depends(get_encryption_service)],
    repository: Annotated[ConnectedAccountRepository, Depends(get_repository)],
    state_signer: Annotated[OAuthStateSigner, Depends(get_state_signer)],
    settings: Annotated[Settings, Depends(get_settings)],
    status_marker: Annotated[StatusMarker, Depends(get_status_marker)],
) -> MetaConnectService:
    return MetaConnectService(
        client=client,
        encryption=encryption,
        repository=repository,
        state_signer=state_signer,
        settings=settings,
        status_marker=status_marker,
    )


Service = Annotated[MetaConnectService, Depends(get_meta_service)]
Resolver = Annotated[BrandAccessResolver, Depends(get_brand_access_resolver)]


async def _authorise(
    resolver: BrandAccessResolver, tenant_id: UUID, brand_id: UUID, allowed: frozenset[BrandRole]
) -> BrandRole:
    """B5 policy: no access ⇒ 404 ``brand_not_found``; wrong role ⇒ 403 ``insufficient_role``."""
    try:
        role = await resolver.resolve(tenant_id, brand_id)
        if role is None:
            raise BrandNotFoundError()
        if role not in allowed:
            raise InsufficientRoleError(f"role {role.value!r} may not perform this action")
    except TenancyError as exc:
        raise tenancy_to_http(exc) from exc
    return role


# ----------------------------------------------------------------- routes --
@router.post(
    f"{BRAND_PREFIX}/connections/start",
    response_model=MetaAuthorizeUrlResponse,
    summary="Start connecting a Meta account to a brand (signed state + Facebook Login URL)",
)
async def start_connection(
    brand_id: UUID,
    actor: CurrentUser,
    service: Service,
    resolver: Resolver,
    payload: ConnectionStartRequest | None = None,
) -> MetaAuthorizeUrlResponse:
    """Body is optional and must be empty (``extra="forbid"``); identity is the JWT."""
    del payload  # validated for shape only
    await _authorise(resolver, actor.tenant_id, brand_id, CONNECTION_WRITE_ROLES)
    return service.start_connection(actor.tenant_id, brand_id, actor.user_id)


@router.get(
    "/social/meta/callback",
    response_model=ConnectedAccountPublic,
    status_code=status.HTTP_201_CREATED,
    summary="OAuth redirect target: complete the flow and persist the connection",
)
async def oauth_callback(
    service: Service,
    resolver: Resolver,
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
    error_description: Annotated[str | None, Query()] = None,
) -> ConnectedAccountPublic:
    if error:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "type": "user_denied" if error == "access_denied" else "provider_error",
                    "message": error_description or error,
                }
            },
        )
    if not code or not state:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"error": {"type": "missing_parameters", "message": "code and state required"}},
        )
    try:
        tenant_id, brand_id = service.verify_state(state)
    except InvalidOAuthStateError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"error": {"type": "invalid_oauth_state", "message": str(exc)}},
        ) from exc
    # B9: authorise BEFORE any Graph call — a grant revoked between start and callback
    # yields 404/403 with no provider traffic and no row.
    await _authorise(resolver, tenant_id, brand_id, CONNECTION_WRITE_ROLES)
    try:
        account = await service.complete_connection(tenant_id, brand_id, code=code)
    except MetaGraphError as exc:
        raise _to_http(exc) from exc
    return ConnectedAccountPublic.from_domain(account, viewer_tenant_id=tenant_id)


@router.get(
    f"{BRAND_PREFIX}/connections",
    response_model=list[ConnectedAccountPublic],
    summary="List a brand's connected Meta accounts (no tokens)",
)
async def list_connections(
    brand_id: UUID, tenant_id: ActorTenantId, service: Service, resolver: Resolver
) -> list[ConnectedAccountPublic]:
    await _authorise(resolver, tenant_id, brand_id, READ_ROLES)
    accounts = await service.list_connections(brand_id)
    return [
        ConnectedAccountPublic.from_domain(account, viewer_tenant_id=tenant_id)
        for account in accounts
    ]


@router.get(
    f"{BRAND_PREFIX}/connections/{{connection_id}}",
    response_model=ConnectedAccountPublic,
    summary="Get one connected Meta account (no tokens)",
)
async def get_connection(
    brand_id: UUID,
    connection_id: UUID,
    tenant_id: ActorTenantId,
    service: Service,
    resolver: Resolver,
) -> ConnectedAccountPublic:
    await _authorise(resolver, tenant_id, brand_id, READ_ROLES)
    account = await service.get_connection(brand_id, connection_id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Connection not found.")
    return ConnectedAccountPublic.from_domain(account, viewer_tenant_id=tenant_id)


@router.put(
    f"{BRAND_PREFIX}/connections/{{connection_id}}/pages/selection",
    response_model=ConnectedAccountPublic,
    summary="Select which Facebook Pages this brand acts on (stores tokens only for them)",
    responses={
        403: {"description": "not_connection_owner — only the connecting tenant may select"},
        422: {"description": "unknown_page_ids / page_not_selectable — nothing was applied"},
    },
)
async def select_pages(
    brand_id: UUID,
    connection_id: UUID,
    payload: PageSelectionRequest,
    actor: CurrentUserFresh,
    service: Service,
    resolver: Resolver,
) -> ConnectedAccountPublic:
    await _authorise(resolver, actor.tenant_id, brand_id, CONNECTION_WRITE_ROLES)
    try:
        account = await service.select_pages(
            brand_id, connection_id, payload.page_ids, actor_tenant_id=actor.tenant_id
        )
    except NotConnectionOwnerError as exc:
        raise _not_owner(exc) from exc
    except UnknownPageIdsError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "unknown_page_ids", "page_ids": exc.page_ids},
        ) from exc
    except PageNotSelectableError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "page_not_selectable", "page_ids": exc.page_ids},
        ) from exc
    except MetaGraphError as exc:
        raise _to_http(exc) from exc
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Connection not found.")
    return ConnectedAccountPublic.from_domain(account, viewer_tenant_id=actor.tenant_id)


@router.delete(
    f"{BRAND_PREFIX}/connections/{{connection_id}}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disconnect a Meta account (removes stored tokens and pages)",
)
async def disconnect(
    brand_id: UUID,
    connection_id: UUID,
    actor: CurrentUserFresh,
    service: Service,
    resolver: Resolver,
) -> Response:
    role = await _authorise(resolver, actor.tenant_id, brand_id, CONNECTION_WRITE_ROLES)
    try:
        deleted = await service.disconnect(
            brand_id, connection_id, actor_tenant_id=actor.tenant_id, actor_role=role
        )
    except NotConnectionOwnerError as exc:
        raise _not_owner(exc) from exc
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Connection not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------- error mapping --
def _not_owner(exc: NotConnectionOwnerError) -> HTTPException:
    return HTTPException(
        status.HTTP_403_FORBIDDEN, detail={"error": exc.error, "message": exc.message}
    )


def _required_action(exc: MetaGraphError) -> str | None:
    """Tell the client what to do next (machine-readable, see SPRINT-01 review §5)."""
    if isinstance(exc, MetaRateLimitError):
        return "retry_later"
    if isinstance(exc, MetaTokenExpiredError | MetaOAuthError):
        return "reconnect"
    if isinstance(exc, MetaPermissionError):
        return "reauthorize_with_additional_scopes"
    return None


def _to_http(exc: MetaGraphError) -> HTTPException:
    detail = {
        "provider": "meta",
        "error": type(exc).__name__,
        "message": exc.message,
        "code": exc.code,
        "subcode": exc.subcode,
        "fbtrace_id": exc.fbtrace_id,
        "required_action": _required_action(exc),
    }
    if isinstance(exc, MetaRateLimitError):
        headers = (
            {"Retry-After": str(int(exc.retry_after_seconds))}
            if exc.retry_after_seconds is not None
            else None
        )
        return HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail=detail, headers=headers)
    if isinstance(exc, MetaOAuthError):
        return HTTPException(status.HTTP_401_UNAUTHORIZED, detail=detail)
    if isinstance(exc, MetaPermissionError):
        return HTTPException(status.HTTP_403_FORBIDDEN, detail=detail)
    if isinstance(exc, MetaTransientError | MetaNetworkError):
        return HTTPException(status.HTTP_502_BAD_GATEWAY, detail=detail)
    return HTTPException(status.HTTP_400_BAD_REQUEST, detail=detail)
