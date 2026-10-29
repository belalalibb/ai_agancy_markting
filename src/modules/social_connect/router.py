"""HTTP routes for the Meta connector (``/api/v1/social/meta/*``) — SPRINT-02 §5-D.

| Method | Path                       | Purpose                                   |
|--------|----------------------------|-------------------------------------------|
| POST   | /connections/start         | issue signed ``state`` + Facebook Login URL |
| GET    | /callback                  | Meta redirect: complete flow and persist  |
| GET    | /connections               | list a tenant's connected accounts        |
| GET    | /connections/{id}          | one connected account                     |
| DELETE | /connections/{id}          | disconnect (DB only)                      |

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
from src.modules.social_connect.domain.repository import ConnectedAccountRepository
from src.modules.social_connect.exceptions import (
    MetaGraphError,
    MetaNetworkError,
    MetaOAuthError,
    MetaPermissionError,
    MetaRateLimitError,
    MetaTokenExpiredError,
    MetaTransientError,
)
from src.modules.social_connect.infrastructure.postgres_repository import (
    PostgresConnectedAccountRepository,
)
from src.modules.social_connect.meta_client import MetaGraphClient
from src.modules.social_connect.schemas import (
    ConnectedAccountPublic,
    ConnectionStartRequest,
    MetaAuthorizeUrlResponse,
)
from src.modules.social_connect.service import MetaConnectService

router = APIRouter(prefix="/social/meta", tags=["Social Connect — Meta"])

# TEMPORARY until auth module: tenant is an explicit query parameter on read routes.
TenantQuery = Annotated[str, Query(min_length=1, max_length=64, description="Tenant id")]


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


def get_meta_service(
    client: Annotated[MetaGraphClient, Depends(get_meta_client)],
    encryption: Annotated[TokenEncryptionService, Depends(get_encryption_service)],
    repository: Annotated[ConnectedAccountRepository, Depends(get_repository)],
    state_signer: Annotated[OAuthStateSigner, Depends(get_state_signer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> MetaConnectService:
    return MetaConnectService(
        client=client,
        encryption=encryption,
        repository=repository,
        state_signer=state_signer,
        settings=settings,
    )


# ----------------------------------------------------------------- routes --
@router.post(
    "/connections/start",
    response_model=MetaAuthorizeUrlResponse,
    summary="Start connecting a Meta account (signed state + Facebook Login URL)",
)
def start_connection(
    payload: ConnectionStartRequest,
    service: Annotated[MetaConnectService, Depends(get_meta_service)],
) -> MetaAuthorizeUrlResponse:
    return service.start_connection(payload.tenant_id)


@router.get(
    "/callback",
    response_model=ConnectedAccountPublic,
    status_code=status.HTTP_201_CREATED,
    summary="OAuth redirect target: complete the flow and persist the connection",
)
async def oauth_callback(
    service: Annotated[MetaConnectService, Depends(get_meta_service)],
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
        account = await service.complete_connection(state=state, code=code)
    except InvalidOAuthStateError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"error": {"type": "invalid_oauth_state", "message": str(exc)}},
        ) from exc
    except MetaGraphError as exc:
        raise _to_http(exc) from exc
    return ConnectedAccountPublic.from_domain(account)


@router.get(
    "/connections",
    response_model=list[ConnectedAccountPublic],
    summary="List a tenant's connected Meta accounts (no tokens)",
)
async def list_connections(
    tenant_id: TenantQuery,
    service: Annotated[MetaConnectService, Depends(get_meta_service)],
) -> list[ConnectedAccountPublic]:
    accounts = await service.list_connections(tenant_id)
    return [ConnectedAccountPublic.from_domain(account) for account in accounts]


@router.get(
    "/connections/{connection_id}",
    response_model=ConnectedAccountPublic,
    summary="Get one connected Meta account (no tokens)",
)
async def get_connection(
    connection_id: UUID,
    tenant_id: TenantQuery,
    service: Annotated[MetaConnectService, Depends(get_meta_service)],
) -> ConnectedAccountPublic:
    account = await service.get_connection(tenant_id, connection_id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Connection not found.")
    return ConnectedAccountPublic.from_domain(account)


@router.delete(
    "/connections/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disconnect a Meta account (removes stored tokens and pages)",
)
async def disconnect(
    connection_id: UUID,
    tenant_id: TenantQuery,
    service: Annotated[MetaConnectService, Depends(get_meta_service)],
) -> Response:
    deleted = await service.disconnect(tenant_id, connection_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Connection not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------- error mapping --
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
