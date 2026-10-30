"""FastAPI dependencies for auth (A9) — the **only** place a request identity is read.

``CurrentUser``       — verifies the Bearer JWT (signature/exp/iss/aud); **no database**.
``CurrentUserFresh``  — ``CurrentUser`` + one query (``refresh_sessions.revoked_at IS NULL``
                        for ``sid``); used by destructive routes (grant revoke, disconnect,
                        page selection) so a logout takes effect immediately there.

Import direction (D-013): this module imports nothing from ``tenancy``; ``tenancy.dependencies``
imports :data:`CurrentUser` from here to build ``ActorTenantId``.
"""

from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.core.database import get_db_session
from src.modules.auth.domain.hashing import PasswordHasher
from src.modules.auth.domain.models import AuthenticatedActor
from src.modules.auth.domain.rate_limit import RateLimiter
from src.modules.auth.domain.repository import RefreshSessionRepository, UserRepository
from src.modules.auth.exceptions import AuthError, SessionRevokedError
from src.modules.auth.infrastructure.argon2_hasher import Argon2PasswordHasher
from src.modules.auth.infrastructure.postgres_repository import (
    PostgresRefreshSessionRepository,
    PostgresUserRepository,
)
from src.modules.auth.infrastructure.rate_limit import InMemoryRateLimiter
from src.modules.auth.jwt import AccessTokenCodec

BEARER_HEADER = {"WWW-Authenticate": "Bearer"}

# ``scheme_name`` becomes the OpenAPI ``components.securitySchemes`` key (acceptance: bearerAuth).
bearer_scheme = HTTPBearer(
    scheme_name="bearerAuth",
    auto_error=False,
    description="Access JWT from /auth/login, /auth/register or /auth/refresh.",
)

_hasher_singleton = Argon2PasswordHasher()
_rate_limiter_singleton = InMemoryRateLimiter()


# ---------------------------------------------------------------- factories --
def get_user_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserRepository:
    return PostgresUserRepository(session)


def get_refresh_session_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> RefreshSessionRepository:
    return PostgresRefreshSessionRepository(session)


def get_password_hasher() -> PasswordHasher:
    return _hasher_singleton


def get_rate_limiter() -> RateLimiter:
    return _rate_limiter_singleton


def get_access_token_codec(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AccessTokenCodec:
    assert settings.auth_jwt_secret is not None  # guaranteed by Settings validator  # noqa: S101
    return AccessTokenCodec(
        secret=settings.auth_jwt_secret,
        issuer=settings.app_name,
        ttl_seconds=settings.auth_access_ttl_seconds,
    )


# ------------------------------------------------------------------ identity --
def to_http(exc: AuthError) -> HTTPException:
    """Map a domain ``AuthError`` to ``HTTPException`` with the standard error body."""
    headers: dict[str, str] = {}
    if exc.status_code == status.HTTP_401_UNAUTHORIZED:
        headers.update(BEARER_HEADER)
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return HTTPException(
        status_code=exc.status_code,
        detail={"error": exc.error, "message": exc.message},
        headers=headers or None,
    )


def _unauthenticated(error: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": error, "message": message},
        headers=BEARER_HEADER,
    )


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    codec: Annotated[AccessTokenCodec, Depends(get_access_token_codec)],
) -> AuthenticatedActor:
    if credentials is None or not credentials.credentials:
        raise _unauthenticated("unauthenticated", "Bearer access token required")
    try:
        return codec.decode(credentials.credentials)
    except jwt.ExpiredSignatureError as exc:
        raise _unauthenticated("token_expired", "access token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise _unauthenticated("token_invalid", "access token invalid") from exc


CurrentUser = Annotated[AuthenticatedActor, Depends(get_current_user)]


async def get_optional_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    codec: Annotated[AccessTokenCodec, Depends(get_access_token_codec)],
) -> AuthenticatedActor | None:
    """``None`` when no Bearer header is present; a *present but invalid* token is still 401."""
    if credentials is None or not credentials.credentials:
        return None
    return await get_current_user(credentials, codec)


OptionalCurrentUser = Annotated[AuthenticatedActor | None, Depends(get_optional_current_user)]


async def get_current_user_fresh(
    actor: CurrentUser,
    sessions: Annotated[RefreshSessionRepository, Depends(get_refresh_session_repository)],
) -> AuthenticatedActor:
    """``CurrentUser`` plus a revocation check against ``refresh_sessions`` (one query)."""
    if not await sessions.is_active(actor.sid):
        raise to_http(SessionRevokedError("session has been revoked"))
    return actor


CurrentUserFresh = Annotated[AuthenticatedActor, Depends(get_current_user_fresh)]


# ------------------------------------------------------------- rate limiting --
IP_LIMIT = 10
IP_WINDOW_SECONDS = 60
LOGIN_FAILURE_LIMIT = 5
LOGIN_BLOCK_SECONDS = 300
IP_BUCKET = "auth:ip"
EMAIL_BUCKET = "auth:login-failures"


def client_ip(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"
