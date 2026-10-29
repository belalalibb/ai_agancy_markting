"""HTTP routes for the Meta connector (``/api/v1/social/meta/*``)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.core.config import Settings, get_settings
from src.core.security import (
    EncryptionKeyMissingError,
    TokenEncryptionService,
    get_token_encryption_service,
)
from src.modules.social_connect.exceptions import (
    MetaGraphError,
    MetaNetworkError,
    MetaOAuthError,
    MetaPermissionError,
    MetaRateLimitError,
    MetaTransientError,
)
from src.modules.social_connect.meta_client import MetaGraphClient
from src.modules.social_connect.schemas import (
    MetaAuthorizeUrlResponse,
    MetaAuthRequest,
    MetaConnectionResult,
    MetaTokenResponse,
)
from src.modules.social_connect.service import MetaConnectService

router = APIRouter(prefix="/social/meta", tags=["Social Connect — Meta"])


# ------------------------------------------------------------ dependencies --
def get_meta_client(settings: Annotated[Settings, Depends(get_settings)]) -> MetaGraphClient:
    return MetaGraphClient(settings=settings)


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


def get_meta_service(
    client: Annotated[MetaGraphClient, Depends(get_meta_client)],
    encryption: Annotated[TokenEncryptionService, Depends(get_encryption_service)],
) -> MetaConnectService:
    return MetaConnectService(client=client, encryption=encryption)


# ----------------------------------------------------------------- routes --
@router.get(
    "/authorize-url",
    response_model=MetaAuthorizeUrlResponse,
    summary="Build the Facebook Login dialog URL",
)
def authorize_url(
    client: Annotated[MetaGraphClient, Depends(get_meta_client)],
    settings: Annotated[Settings, Depends(get_settings)],
    redirect_uri: Annotated[str | None, Query()] = None,
) -> MetaAuthorizeUrlResponse:
    # Deliberately does *not* depend on the encryption service: building the login
    # URL touches no secrets, so it must keep working even if encryption is misconfigured.
    url, state = client.build_authorize_url(redirect_uri or settings.meta_oauth_redirect_uri)
    return MetaAuthorizeUrlResponse(authorize_url=url, state=state)


@router.post(
    "/token",
    response_model=MetaTokenResponse,
    summary="Exchange an authorization code for a long-lived user token",
)
async def exchange_token(
    payload: MetaAuthRequest,
    service: Annotated[MetaConnectService, Depends(get_meta_service)],
) -> MetaTokenResponse:
    try:
        return await service.exchange_code(payload)
    except MetaGraphError as exc:
        raise _to_http(exc) from exc


@router.post(
    "/connect",
    response_model=MetaConnectionResult,
    status_code=status.HTTP_201_CREATED,
    summary="Complete OAuth: exchange code, list pages/IG accounts, encrypt token",
)
async def connect(
    payload: MetaAuthRequest,
    service: Annotated[MetaConnectService, Depends(get_meta_service)],
) -> MetaConnectionResult:
    try:
        return await service.connect(payload)
    except MetaGraphError as exc:
        raise _to_http(exc) from exc


# ---------------------------------------------------------- error mapping --
def _to_http(exc: MetaGraphError) -> HTTPException:
    detail = {
        "provider": "meta",
        "error": type(exc).__name__,
        "message": exc.message,
        "code": exc.code,
        "subcode": exc.subcode,
        "fbtrace_id": exc.fbtrace_id,
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
