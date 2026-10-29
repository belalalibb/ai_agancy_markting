"""Shared pytest fixtures.

Everything here is hermetic: no network, no database, no real secrets. Settings are
injected through environment variables with obviously fake values.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import respx
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.core.config import Settings, get_settings
from src.core.security import TokenEncryptionService
from src.modules.social_connect.meta_client import MetaGraphClient

from tests.fakes import FakeConnectedAccountRepository

FAKE_APP_ID = "123456789012345"
FAKE_APP_SECRET = "fake-app-secret-for-tests-only"
FAKE_STATE_SECRET = "fake-oauth-state-secret-for-tests-only"
GRAPH_VERSION = "v26.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"


@pytest.fixture(scope="session")
def fernet_key() -> str:
    return Fernet.generate_key().decode("ascii")


@pytest.fixture(autouse=True)
def _test_env(monkeypatch: pytest.MonkeyPatch, fernet_key: str) -> Iterator[None]:
    """Force deterministic, secret-free settings for every test."""
    env = {
        "APP_ENV": "test",
        "DEBUG": "false",
        "TOKEN_ENCRYPTION_KEY": fernet_key,
        "META_APP_ID": FAKE_APP_ID,
        "META_APP_SECRET": FAKE_APP_SECRET,
        "META_GRAPH_API_VERSION": GRAPH_VERSION,
        "OAUTH_STATE_SECRET": FAKE_STATE_SECRET,
        "META_HTTP_MAX_RETRIES": "2",
        "META_HTTP_TIMEOUT_SECONDS": "1",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.fixture
def encryption(fernet_key: str) -> TokenEncryptionService:
    return TokenEncryptionService(fernet_key)


@pytest.fixture
def graph_mock() -> Iterator[respx.MockRouter]:
    """A ``respx`` router bound to the Graph API base URL; unmatched calls fail loudly."""
    with respx.mock(base_url=GRAPH_BASE, assert_all_called=False) as router:
        yield router


@pytest.fixture
async def meta_client(settings: Settings) -> AsyncIterator[MetaGraphClient]:
    """Client with a no-op sleep so retry tests run instantly."""

    async def _no_sleep(_: float) -> None:
        return None

    async with httpx.AsyncClient() as http:
        yield MetaGraphClient(settings=settings, http_client=http, sleep=_no_sleep)


@pytest.fixture
def fake_repo() -> FakeConnectedAccountRepository:
    return FakeConnectedAccountRepository()


@pytest.fixture
def app(settings: Settings, fake_repo: FakeConnectedAccountRepository) -> FastAPI:
    """App wired to the fake repository — isolated tests never touch PostgreSQL."""
    from src.main import create_app
    from src.modules.social_connect.router import get_repository

    application = create_app(settings)
    application.dependency_overrides[get_repository] = lambda: fake_repo
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


# ----------------------------------------------------------- payload factories
def token_payload(
    token: str = "SHORT_LIVED_TOKEN",  # noqa: S107 - fake fixture value
    expires_in: int = 5183944,
) -> dict[str, Any]:
    return {"access_token": token, "token_type": "bearer", "expires_in": expires_in}


def pages_payload() -> dict[str, Any]:
    return {
        "data": [
            {
                "id": "1001",
                "name": "Nile Coffee Roasters",
                "category": "Coffee shop",
                "tasks": ["ANALYZE", "ADVERTISE", "MODERATE", "CREATE_CONTENT", "MANAGE"],
                "access_token": "PAGE_TOKEN_1001",
                "instagram_business_account": {"id": "17841400000001", "username": "nilecoffee"},
            },
            {
                "id": "1002",
                "name": "Cairo Dental Clinic",
                "category": "Dentist",
                "tasks": ["ANALYZE", "CREATE_CONTENT"],
                "access_token": "PAGE_TOKEN_1002",
            },
        ],
        "paging": {"cursors": {"before": "a", "after": "b"}},
    }


def me_payload(user_id: str = "10001", name: str | None = "Nile Owner") -> dict[str, Any]:
    """``GET /me?fields=id,name`` — the trusted identity (SPRINT-02.1 F2)."""
    data: dict[str, Any] = {"id": user_id}
    if name is not None:
        data["name"] = name
    return data


def debug_token_payload(
    user_id: str = "10001",
    scopes: list[str] | None = None,
    expires_at: int | None = 1_900_000_000,
    *,
    app_id: str = FAKE_APP_ID,
    is_valid: bool = True,
    error_code: int | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "app_id": app_id,
        "type": "USER",
        "application": "AI Growth OS",
        "is_valid": is_valid,
        "user_id": user_id,
        "scopes": scopes if scopes is not None else ["pages_show_list", "instagram_basic"],
    }
    if expires_at is not None:
        data["expires_at"] = expires_at
    if error_code is not None:
        data["error"] = {"code": error_code, "message": "Invalid OAuth access token."}
    return {"data": data}


def oauth_error_payload(
    message: str = "Error validating access token: Session has expired",
    code: int = 190,
    subcode: int | None = 463,
    error_type: str = "OAuthException",
) -> dict[str, Any]:
    err: dict[str, Any] = {
        "message": message,
        "type": error_type,
        "code": code,
        "fbtrace_id": "AbCdEf",
    }
    if subcode is not None:
        err["error_subcode"] = subcode
    return {"error": err}
