"""Integration tests — FastAPI routes for the Meta connector + app wiring (/docs, /health)."""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.core.config import Settings, get_settings
from src.core.security import TokenEncryptionService
from src.main import app as module_level_app
from src.modules.social_connect.exceptions import (
    MetaGraphError,
    MetaNetworkError,
    MetaOAuthError,
    MetaPermissionError,
    MetaRateLimitError,
    MetaTokenExpiredError,
    MetaTransientError,
)
from src.modules.social_connect.router import _to_http

from tests.conftest import FAKE_APP_SECRET, oauth_error_payload, pages_payload, token_payload

pytestmark = pytest.mark.contract

AUTH_BODY = {"code": "AUTH_CODE", "redirect_uri": "https://app.example.com/cb"}


def _stub_oauth(graph_mock: respx.MockRouter) -> None:
    graph_mock.get("/oauth/access_token", params={"grant_type": "fb_exchange_token"}).mock(
        return_value=httpx.Response(200, json=token_payload("LONG_LIVED", 5183944))
    )
    graph_mock.get("/oauth/access_token").mock(
        return_value=httpx.Response(200, json=token_payload("SHORT_LIVED", 5000))
    )


# ------------------------------------------------------------------ app ---
def test_health(client: TestClient) -> None:
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    assert res.json()["env"] == "test"


def test_swagger_docs_served(client: TestClient) -> None:
    res = client.get("/docs")
    assert res.status_code == 200
    assert "swagger-ui" in res.text.lower()


def test_redoc_served(client: TestClient) -> None:
    assert client.get("/redoc").status_code == 200


def test_openapi_lists_meta_routes(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    assert "/api/v1/social/meta/authorize-url" in paths
    assert "/api/v1/social/meta/token" in paths
    assert "/api/v1/social/meta/connect" in paths
    assert "/health" in paths
    assert schema["info"]["title"] == "AI Growth OS"


def test_module_level_app_boots() -> None:
    with TestClient(module_level_app) as c:
        assert c.get("/docs").status_code == 200
        assert c.app.state.settings is not None  # type: ignore[attr-defined]


# -------------------------------------------------------- authorize-url ---
def test_authorize_url_endpoint(client: TestClient) -> None:
    res = client.get("/api/v1/social/meta/authorize-url", params={"redirect_uri": "https://a.b/c"})
    assert res.status_code == 200
    body = res.json()
    assert body["authorize_url"].startswith("https://www.facebook.com/")
    assert "redirect_uri=https%3A%2F%2Fa.b%2Fc" in body["authorize_url"]
    assert body["state"]
    assert FAKE_APP_SECRET not in body["authorize_url"]


def test_authorize_url_uses_default_redirect(client: TestClient) -> None:
    res = client.get("/api/v1/social/meta/authorize-url")
    assert res.status_code == 200
    assert "localhost%3A8000" in res.json()["authorize_url"]


# ---------------------------------------------------------------- token ---
def test_token_exchange_success_masks_tokens(
    client: TestClient, graph_mock: respx.MockRouter
) -> None:
    _stub_oauth(graph_mock)
    res = client.post("/api/v1/social/meta/token", json=AUTH_BODY)
    assert res.status_code == 200
    body = res.json()
    assert body["long_lived_token"] == "***"
    assert body["short_lived_token"] == "***"
    assert body["expires_in"] == 5183944
    assert body["expires_at"] is not None
    assert "LONG_LIVED" not in res.text


def test_token_exchange_validation_error(client: TestClient) -> None:
    res = client.post("/api/v1/social/meta/token", json={"code": "", "redirect_uri": "nope"})
    assert res.status_code == 422


def test_token_exchange_invalid_code_returns_401(
    client: TestClient, graph_mock: respx.MockRouter
) -> None:
    graph_mock.get("/oauth/access_token").mock(
        return_value=httpx.Response(
            400, json=oauth_error_payload("Invalid verification code format.", 100, None)
        )
    )
    res = client.post("/api/v1/social/meta/token", json=AUTH_BODY)
    assert res.status_code == 401
    detail = res.json()["detail"]
    assert detail["error"] == "MetaOAuthError"
    assert detail["code"] == 100


# -------------------------------------------------------------- connect ---
def test_connect_success(
    client: TestClient, graph_mock: respx.MockRouter, encryption: TokenEncryptionService
) -> None:
    _stub_oauth(graph_mock)
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, json=pages_payload()))

    res = client.post("/api/v1/social/meta/connect", json=AUTH_BODY)
    assert res.status_code == 201
    body = res.json()
    assert body["provider"] == "meta"
    assert encryption.decrypt_token(body["encrypted_user_token"]) == "LONG_LIVED"
    assert [p["page_id"] for p in body["pages"]] == ["1001", "1002"]
    assert body["pages"][0]["instagram_business_account_id"] == "17841400000001"
    assert body["pages"][0]["page_access_token"] == "***"
    assert body["instagram_accounts"] == ["17841400000001"]
    assert "LONG_LIVED" not in res.text
    assert "PAGE_TOKEN" not in res.text


def test_connect_expired_token_returns_401(
    client: TestClient, graph_mock: respx.MockRouter
) -> None:
    _stub_oauth(graph_mock)
    graph_mock.get("/me/accounts").mock(
        return_value=httpx.Response(400, json=oauth_error_payload(code=190, subcode=463))
    )
    res = client.post("/api/v1/social/meta/connect", json=AUTH_BODY)
    assert res.status_code == 401
    detail = res.json()["detail"]
    assert detail["error"] == "MetaTokenExpiredError"
    assert detail["code"] == 190
    assert detail["subcode"] == 463
    assert detail["fbtrace_id"] == "AbCdEf"


def test_connect_rate_limited_returns_429_with_retry_after(
    client: TestClient, graph_mock: respx.MockRouter
) -> None:
    _stub_oauth(graph_mock)
    graph_mock.get("/me/accounts").mock(
        return_value=httpx.Response(
            429,
            json={"error": {"message": "limit", "code": 4}},
            headers={"Retry-After": "12"},
        )
    )
    res = client.post("/api/v1/social/meta/connect", json=AUTH_BODY)
    assert res.status_code == 429
    assert res.headers["Retry-After"] == "12"
    assert res.json()["detail"]["error"] == "MetaRateLimitError"


def test_connect_rate_limited_without_retry_after(
    client: TestClient, graph_mock: respx.MockRouter
) -> None:
    _stub_oauth(graph_mock)
    graph_mock.get("/me/accounts").mock(
        return_value=httpx.Response(429, json={"error": {"message": "limit", "code": 4}})
    )
    res = client.post("/api/v1/social/meta/connect", json=AUTH_BODY)
    assert res.status_code == 429
    assert "Retry-After" not in res.headers


def test_connect_permission_error_returns_403(
    client: TestClient, graph_mock: respx.MockRouter
) -> None:
    _stub_oauth(graph_mock)
    graph_mock.get("/me/accounts").mock(
        return_value=httpx.Response(
            403,
            json={
                "error": {
                    "message": "(#10) denied",
                    "code": 10,
                    "type": "GraphMethodException",
                }
            },
        )
    )
    res = client.post("/api/v1/social/meta/connect", json=AUTH_BODY)
    assert res.status_code == 403
    assert res.json()["detail"]["error"] == "MetaPermissionError"


def test_connect_upstream_failure_returns_502(
    client: TestClient, graph_mock: respx.MockRouter
) -> None:
    _stub_oauth(graph_mock)
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(500, text="oops"))
    res = client.post("/api/v1/social/meta/connect", json=AUTH_BODY)
    assert res.status_code == 502
    assert res.json()["detail"]["error"] == "MetaTransientError"


def test_connect_network_failure_returns_502(
    client: TestClient, graph_mock: respx.MockRouter
) -> None:
    _stub_oauth(graph_mock)
    graph_mock.get("/me/accounts").mock(side_effect=httpx.ConnectError("refused"))
    res = client.post("/api/v1/social/meta/connect", json=AUTH_BODY)
    assert res.status_code == 502
    assert res.json()["detail"]["error"] == "MetaNetworkError"


def test_connect_unknown_graph_error_returns_400(
    client: TestClient, graph_mock: respx.MockRouter
) -> None:
    _stub_oauth(graph_mock)
    graph_mock.get("/me/accounts").mock(
        return_value=httpx.Response(
            400,
            json={
                "error": {
                    "message": "Unsupported",
                    "code": 100,
                    "type": "GraphMethodException",
                }
            },
        )
    )
    res = client.post("/api/v1/social/meta/connect", json=AUTH_BODY)
    assert res.status_code == 400
    assert res.json()["detail"]["error"] == "MetaGraphError"


# ------------------------------------------------- encryption misconfig ---
def test_missing_encryption_key_returns_503(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    no_key_settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert no_key_settings.token_encryption_key is None
    app.dependency_overrides[get_settings] = lambda: no_key_settings
    try:
        with TestClient(app) as c:
            res = c.post("/api/v1/social/meta/connect", json=AUTH_BODY)
        assert res.status_code == 503
        assert "encryption" in res.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()


def test_authorize_url_works_without_encryption_key(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: building the login URL must not require TOKEN_ENCRYPTION_KEY."""
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)  # type: ignore[call-arg]
    try:
        with TestClient(app) as c:
            res = c.get("/api/v1/social/meta/authorize-url")
        assert res.status_code == 200
        assert res.json()["authorize_url"].startswith("https://www.facebook.com/")
    finally:
        app.dependency_overrides.clear()


# ----------------------------------------- SPRINT-02 A4: required_action --
@pytest.mark.parametrize(
    ("exc", "expected_status", "expected_action"),
    [
        (MetaTokenExpiredError("expired", code=190), 401, "reconnect"),
        (MetaOAuthError("bad code", code=100), 401, "reconnect"),
        (
            MetaPermissionError("(#200) missing", code=200),
            403,
            "reauthorize_with_additional_scopes",
        ),
        (MetaRateLimitError("limit", code=4), 429, "retry_later"),
        (MetaTransientError("oops", http_status=503), 502, None),
        (MetaNetworkError("timeout"), 502, None),
        (MetaGraphError("unknown", code=100), 400, None),
    ],
)
def test_required_action_in_error_detail(
    exc: MetaGraphError, expected_status: int, expected_action: str | None
) -> None:
    http_exc = _to_http(exc)
    assert http_exc.status_code == expected_status
    assert http_exc.detail["required_action"] == expected_action  # type: ignore[index]
