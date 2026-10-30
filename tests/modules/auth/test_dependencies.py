"""Unit tests — ``auth.dependencies`` factories and the identity guards (A-U8/A-U9)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from src.core.config import Settings
from src.modules.auth.dependencies import (
    get_access_token_codec,
    get_current_user,
    get_current_user_fresh,
    get_optional_current_user,
    get_password_hasher,
    get_rate_limiter,
    get_refresh_session_repository,
    get_user_repository,
)
from src.modules.auth.domain.models import AuthenticatedActor
from src.modules.auth.infrastructure.argon2_hasher import Argon2PasswordHasher
from src.modules.auth.infrastructure.postgres_repository import (
    PostgresRefreshSessionRepository,
    PostgresUserRepository,
)
from src.modules.auth.infrastructure.rate_limit import InMemoryRateLimiter
from src.modules.auth.jwt import AccessTokenCodec

from tests.conftest import OWNER_ID, OWNER_USER_ID
from tests.fakes import FakeRefreshSessionRepository, FakeStore, StubSession

pytestmark = pytest.mark.unit


def test_factories_return_postgres_backed_singletons(settings: Settings) -> None:
    session = StubSession()
    assert isinstance(get_user_repository(session), PostgresUserRepository)  # type: ignore[arg-type]
    assert isinstance(
        get_refresh_session_repository(session),  # type: ignore[arg-type]
        PostgresRefreshSessionRepository,
    )
    assert isinstance(get_password_hasher(), Argon2PasswordHasher)
    assert get_password_hasher() is get_password_hasher()  # process-wide singleton
    assert isinstance(get_rate_limiter(), InMemoryRateLimiter)
    assert get_rate_limiter() is get_rate_limiter()
    codec = get_access_token_codec(settings)
    assert isinstance(codec, AccessTokenCodec)
    assert codec.ttl_seconds == settings.auth_access_ttl_seconds


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


async def test_current_user_decodes_without_db(codec: AccessTokenCodec) -> None:
    actor = AuthenticatedActor(user_id=OWNER_USER_ID, tenant_id=OWNER_ID, sid=UUID(int=7))
    assert await get_current_user(_creds(codec.issue(actor)), codec) == actor


async def test_current_user_expired_token_401_token_expired(codec: AccessTokenCodec) -> None:
    actor = AuthenticatedActor(user_id=OWNER_USER_ID, tenant_id=OWNER_ID, sid=UUID(int=7))
    stale = codec.issue(actor, now=datetime.now(tz=UTC) - timedelta(hours=1))
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(_creds(stale), codec)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"] == "token_expired"
    assert exc_info.value.headers["WWW-Authenticate"] == "Bearer"


async def test_current_user_missing_or_empty_credentials_401(codec: AccessTokenCodec) -> None:
    for creds in (None, _creds("")):
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(creds, codec)
        assert exc_info.value.detail["error"] == "unauthenticated"


async def test_optional_current_user(codec: AccessTokenCodec) -> None:
    assert await get_optional_current_user(None, codec) is None
    actor = AuthenticatedActor(user_id=OWNER_USER_ID, tenant_id=OWNER_ID, sid=UUID(int=9))
    assert await get_optional_current_user(_creds(codec.issue(actor)), codec) == actor
    with pytest.raises(HTTPException):  # present-but-invalid is still refused
        await get_optional_current_user(_creds("bad.token.value"), codec)


async def test_current_user_fresh_passes_for_active_session(store: FakeStore) -> None:
    from src.modules.auth.domain.models import RefreshSession

    session = RefreshSession(
        user_id=OWNER_USER_ID,
        token_hash="0" * 64,
        expires_at=datetime.now(tz=UTC) + timedelta(days=1),
    )
    store.sessions[session.id] = session
    actor = AuthenticatedActor(user_id=OWNER_USER_ID, tenant_id=OWNER_ID, sid=session.id)
    assert await get_current_user_fresh(actor, FakeRefreshSessionRepository(store)) == actor
    # Unknown sid (never issued) is treated as revoked.
    ghost = AuthenticatedActor(user_id=OWNER_USER_ID, tenant_id=OWNER_ID, sid=UUID(int=1))
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user_fresh(ghost, FakeRefreshSessionRepository(store))
    assert exc_info.value.detail["error"] == "session_revoked"


def test_expired_bearer_over_http_401_token_expired(
    client: TestClient, codec: AccessTokenCodec
) -> None:
    actor = AuthenticatedActor(user_id=OWNER_USER_ID, tenant_id=OWNER_ID, sid=UUID(int=3))
    stale = codec.issue(actor, now=datetime.now(tz=UTC) - timedelta(hours=1))
    response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {stale}"})
    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "token_expired"


def test_auth_headers_factory_issues_valid_tokens(
    auth_headers: Callable[[UUID], dict[str, str]], codec: AccessTokenCodec, store: FakeStore
) -> None:
    headers = auth_headers(OWNER_ID)
    actor = codec.decode(headers["Authorization"].removeprefix("Bearer "))
    assert actor.tenant_id == OWNER_ID and actor.user_id == OWNER_USER_ID
    assert store.sessions[actor.sid].revoked_at is None
    # Unknown tenant ⇒ ad-hoc user id, still a valid token.
    other = codec.decode(auth_headers(UUID(int=42))["Authorization"].removeprefix("Bearer "))
    assert other.tenant_id == UUID(int=42)
