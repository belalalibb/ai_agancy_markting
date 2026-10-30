"""HTTP routes for auth (``/api/v1/auth/*``) — SPRINT-04 A6–A8.

| Method | Path             | Auth                  | Rate limit                   |
|--------|------------------|-----------------------|------------------------------|
| POST   | /auth/register   | —                     | 10/min/IP                    |
| POST   | /auth/login      | —                     | 10/min/IP + 5 failures/email |
| POST   | /auth/refresh    | refresh cookie + CSRF | 10/min/IP                    |
| POST   | /auth/logout     | cookie and/or Bearer  | —                            |
| POST   | /auth/logout-all | Bearer                | —                            |
| GET    | /auth/me         | Bearer                | —                            |

Refresh cookie: ``__Host-refresh`` (``refresh`` in development), ``HttpOnly; SameSite=Strict;
Secure`` (except development), ``Path=/api/v1/auth``, ``Max-Age=AUTH_REFRESH_TTL_SECONDS``.
CSRF (cookie routes): ``X-Requested-With: XMLHttpRequest`` **or** ``Origin`` in the allowlist.
Error bodies: ``{"error": "<code>", "message": "..."}``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from src.core.config import Settings, get_settings
from src.modules.auth.dependencies import (
    EMAIL_BUCKET,
    IP_BUCKET,
    IP_LIMIT,
    IP_WINDOW_SECONDS,
    LOGIN_BLOCK_SECONDS,
    LOGIN_FAILURE_LIMIT,
    CurrentUser,
    OptionalCurrentUser,
    client_ip,
    get_access_token_codec,
    get_password_hasher,
    get_rate_limiter,
    get_refresh_session_repository,
    get_user_repository,
    to_http,
)
from src.modules.auth.domain.hashing import PasswordHasher
from src.modules.auth.domain.rate_limit import RateLimiter
from src.modules.auth.domain.repository import RefreshSessionRepository, UserRepository
from src.modules.auth.exceptions import (
    AuthError,
    CsrfRejectedError,
    InvalidCredentialsError,
    RateLimitedError,
    RefreshMissingError,
)
from src.modules.auth.infrastructure.argon2_hasher import DUMMY_HASH
from src.modules.auth.jwt import AccessTokenCodec
from src.modules.auth.schemas import (
    LoginRequest,
    MeOut,
    RegisterRequest,
    TenantSummaryOut,
    TokenOut,
    UserOut,
)
from src.modules.auth.service import AuthService, ClientInfo, IssuedTokens
from src.modules.tenancy.exceptions import TenancyError
from src.modules.tenancy.router import get_tenancy_service
from src.modules.tenancy.router import to_http as tenancy_to_http
from src.modules.tenancy.service import TenancyService

router = APIRouter(prefix="/auth", tags=["Auth"])

COOKIE_PATH_SUFFIX = "/auth"
CSRF_HEADER = "x-requested-with"
CSRF_HEADER_VALUE = "xmlhttprequest"


# ---------------------------------------------------------------- wiring --
def get_auth_service(
    users: Annotated[UserRepository, Depends(get_user_repository)],
    sessions: Annotated[RefreshSessionRepository, Depends(get_refresh_session_repository)],
    hasher: Annotated[PasswordHasher, Depends(get_password_hasher)],
    codec: Annotated[AccessTokenCodec, Depends(get_access_token_codec)],
    tenancy: Annotated[TenancyService, Depends(get_tenancy_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthService:
    return AuthService(
        users=users,
        sessions=sessions,
        hasher=hasher,
        codec=codec,
        tenancy=tenancy,
        refresh_ttl_seconds=settings.auth_refresh_ttl_seconds,
        dummy_hash=DUMMY_HASH,
    )


Service = Annotated[AuthService, Depends(get_auth_service)]
Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]
AppSettings = Annotated[Settings, Depends(get_settings)]


def _client(request: Request) -> ClientInfo:
    return ClientInfo(user_agent=request.headers.get("user-agent"), ip=client_ip(request))


def _cookie_path(settings: Settings) -> str:
    return f"{settings.api_v1_prefix.rstrip('/')}{COOKIE_PATH_SUFFIX}"


def _set_refresh_cookie(response: Response, settings: Settings, value: str) -> None:
    response.set_cookie(
        key=settings.auth_refresh_cookie_name,
        value=value,
        max_age=settings.auth_refresh_ttl_seconds,
        path=_cookie_path(settings),
        secure=bool(settings.auth_cookie_secure),
        httponly=True,
        samesite="strict",
    )


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.set_cookie(
        key=settings.auth_refresh_cookie_name,
        value="",
        max_age=0,
        expires=0,
        path=_cookie_path(settings),
        secure=bool(settings.auth_cookie_secure),
        httponly=True,
        samesite="strict",
    )


def _token_out(issued: IssuedTokens) -> TokenOut:
    return TokenOut(
        access_token=issued.access_token,
        expires_in=issued.expires_in,
        user=UserOut(id=issued.user.id, email=issued.user.email, tenant_id=issued.user.tenant_id),
    )


def _limit_by_ip(request: Request, limiter: RateLimiter) -> None:
    retry = limiter.hit(IP_BUCKET, client_ip(request), IP_LIMIT, IP_WINDOW_SECONDS)
    if retry is not None:
        raise to_http(RateLimitedError(retry, "too many requests from this address"))


def _require_csrf(request: Request, settings: Settings) -> None:
    """Cookie-authenticated routes: custom header *or* an allow-listed Origin (A6).

    * header present, no ``Origin``                      ⇒ OK (non-browser / same-origin fetch)
    * header present, ``Origin`` present                 ⇒ ``Origin`` must be allow-listed
    * header absent, ``Origin`` allow-listed             ⇒ OK
    * otherwise                                          ⇒ 403 ``csrf_rejected``
    An empty allowlist therefore rejects *every* request that carries an ``Origin``.
    """
    origin = request.headers.get("origin")
    origin_ok = origin is not None and origin.rstrip("/") in settings.auth_allowed_origins_list
    has_header = request.headers.get(CSRF_HEADER, "").lower() == CSRF_HEADER_VALUE
    if origin_ok or (has_header and origin is None):
        return
    raise to_http(CsrfRejectedError("missing X-Requested-With header or disallowed Origin"))


# ---------------------------------------------------------------- routes --
@router.post(
    "/register",
    response_model=TokenOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new tenant + user and sign in",
)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    service: Service,
    limiter: Limiter,
    settings: AppSettings,
) -> TokenOut:
    _limit_by_ip(request, limiter)
    try:
        issued = await service.register(
            email=str(payload.email),
            password=payload.password,
            tenant_slug=payload.tenant_slug,
            tenant_name=payload.tenant_name,
            client=_client(request),
        )
    except AuthError as exc:
        raise to_http(exc) from exc
    except TenancyError as exc:  # tenant_slug_taken ⇒ 409
        raise tenancy_to_http(exc) from exc
    _set_refresh_cookie(response, settings, issued.refresh_cookie_value)
    return _token_out(issued)


@router.post("/login", response_model=TokenOut, summary="Sign in with email + password")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    service: Service,
    limiter: Limiter,
    settings: AppSettings,
) -> TokenOut:
    _limit_by_ip(request, limiter)
    email_key = str(payload.email).lower()
    blocked = limiter.blocked_for(EMAIL_BUCKET, email_key)
    if blocked is not None:
        raise to_http(RateLimitedError(blocked, "too many failed login attempts"))
    try:
        user = await service.login(str(payload.email), payload.password, client=_client(request))
    except InvalidCredentialsError as exc:
        limiter.record_failure(EMAIL_BUCKET, email_key, LOGIN_FAILURE_LIMIT, LOGIN_BLOCK_SECONDS)
        raise to_http(exc) from exc
    except AuthError as exc:
        raise to_http(exc) from exc
    limiter.reset_failures(EMAIL_BUCKET, email_key)
    issued = await service.issue_for(user, client=_client(request))
    _set_refresh_cookie(response, settings, issued.refresh_cookie_value)
    return _token_out(issued)


@router.post("/refresh", response_model=TokenOut, summary="Rotate the refresh session")
async def refresh(
    request: Request,
    response: Response,
    service: Service,
    limiter: Limiter,
    settings: AppSettings,
) -> TokenOut:
    _limit_by_ip(request, limiter)
    _require_csrf(request, settings)
    cookie = request.cookies.get(settings.auth_refresh_cookie_name)
    if not cookie:
        raise to_http(RefreshMissingError("refresh cookie missing"))
    try:
        issued = await service.refresh(cookie, client=_client(request))
    except AuthError as exc:
        http = to_http(exc)
        http.headers = {**(http.headers or {}), **_clear_cookie_header(settings)}
        raise http from exc
    _set_refresh_cookie(response, settings, issued.refresh_cookie_value)
    return _token_out(issued)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke the current session and clear the cookie",
)
async def logout(
    request: Request,
    response: Response,
    actor: OptionalCurrentUser,
    service: Service,
    settings: AppSettings,
) -> Response:
    """Works with the refresh cookie (CSRF-checked) and/or a Bearer token (``sid``)."""
    cookie = request.cookies.get(settings.auth_refresh_cookie_name)
    if cookie:
        _require_csrf(request, settings)
    elif actor is None:
        raise to_http(RefreshMissingError("refresh cookie or Bearer token required"))
    await service.logout(cookie, actor.sid if actor is not None else None)
    _clear_refresh_cookie(response, settings)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post(
    "/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke every session of the current user",
)
async def logout_all(
    actor: CurrentUser, response: Response, service: Service, settings: AppSettings
) -> Response:
    await service.logout_all(actor)
    _clear_refresh_cookie(response, settings)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=MeOut, summary="Current user + tenant")
async def me(actor: CurrentUser, service: Service) -> MeOut:
    try:
        user, tenant = await service.me(actor)
    except AuthError as exc:
        raise to_http(exc) from exc
    except TenancyError as exc:
        raise tenancy_to_http(exc) from exc
    return MeOut(
        id=user.id,
        email=user.email,
        tenant_id=user.tenant_id,
        tenant=TenantSummaryOut(id=tenant.id, slug=tenant.slug, name=tenant.name),
    )


# ---------------------------------------------------------------- helpers --
def _clear_cookie_header(settings: Settings) -> dict[str, str]:
    """``Set-Cookie`` clearing header for error responses raised via ``HTTPException``."""
    scratch = Response()
    _clear_refresh_cookie(scratch, settings)
    return {k.decode(): v.decode() for k, v in scratch.raw_headers if k.lower() == b"set-cookie"}


__all__ = ["HTTPException", "router"]
