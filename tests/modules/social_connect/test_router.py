"""Contract tests — FastAPI routes for the Meta connector + app wiring (/docs, /health).

SPRINT-02 note: the SPRINT-01 tests for ``GET /authorize-url``, ``POST /token`` and
``POST /connect`` were replaced — those routes no longer exist (§5-D). The error-mapping
scenarios (401/403/429/502/400/503) were ported to ``GET /callback``.
"""

from __future__ import annotations

import json
import re
from uuid import uuid4

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.core.config import Settings, get_settings
from src.core.oauth_state import OAuthStateSigner
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

from tests.conftest import (
    FAKE_APP_SECRET,
    FAKE_STATE_SECRET,
    debug_token_payload,
    me_payload,
    oauth_error_payload,
    pages_payload,
    token_payload,
)
from tests.fakes import FakeConnectedAccountRepository

pytestmark = pytest.mark.contract

BASE = "/api/v1/social/meta"
# ``has_page_token`` is a derived boolean (SPRINT-02.1 F4) — the only "token"-named key
# allowed in a public body; everything else matching this pattern is a leak.
SECRET_PATTERNS = re.compile(
    r"(?<!has_page_)token|encrypted|\*\*\*|EAA|LONG_LIVED|PAGE_TOKEN_", re.IGNORECASE
)


@pytest.fixture
def signer() -> OAuthStateSigner:
    return OAuthStateSigner(FAKE_STATE_SECRET)


def _stub_oauth(graph_mock: respx.MockRouter) -> None:
    graph_mock.get("/oauth/access_token", params={"grant_type": "fb_exchange_token"}).mock(
        return_value=httpx.Response(200, json=token_payload("LONG_LIVED", 5183944))
    )
    graph_mock.get("/oauth/access_token").mock(
        return_value=httpx.Response(200, json=token_payload("SHORT_LIVED", 5000))
    )


def _stub_happy_path(graph_mock: respx.MockRouter) -> None:
    _stub_oauth(graph_mock)
    graph_mock.get("/me", params__contains={"fields": "id,name"}).mock(
        return_value=httpx.Response(200, json=me_payload())
    )
    graph_mock.get("/debug_token").mock(
        return_value=httpx.Response(200, json=debug_token_payload())
    )
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, json=pages_payload()))


def _callback(
    client: TestClient, signer: OAuthStateSigner, tenant: str = "demo"
) -> httpx.Response:
    params = {"code": "CODE", "state": signer.issue(tenant)}
    return client.get(f"{BASE}/callback", params=params)


def _assert_no_secrets(body: str) -> None:
    assert not SECRET_PATTERNS.search(body), body


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


def test_openapi_lists_exactly_the_new_meta_routes(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    assert set(paths) == {
        f"{BASE}/connections/start",
        f"{BASE}/callback",
        f"{BASE}/connections",
        f"{BASE}/connections/{{connection_id}}",
        "/health",
        "/health/ready",
    }
    assert set(paths[f"{BASE}/connections/{{connection_id}}"]) == {"get", "delete"}
    assert schema["info"]["title"] == "AI Growth OS"


def test_removed_routes_return_404(client: TestClient) -> None:
    assert client.get(f"{BASE}/authorize-url").status_code in (404, 405)
    assert client.post(f"{BASE}/token", json={}).status_code in (404, 405)
    assert client.post(f"{BASE}/connect", json={}).status_code in (404, 405)


def test_module_level_app_boots_and_owns_shared_http_client() -> None:
    with TestClient(module_level_app) as c:
        assert c.get("/docs").status_code == 200
        assert isinstance(module_level_app.state.http_client, httpx.AsyncClient)
        assert not module_level_app.state.http_client.is_closed
    assert module_level_app.state.http_client.is_closed  # closed on shutdown


# ---------------------------------------------------------------- start ---
def test_start_connection_returns_signed_state_and_url(
    client: TestClient, signer: OAuthStateSigner
) -> None:
    res = client.post(f"{BASE}/connections/start", json={"tenant_id": "demo"})
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"authorization_url", "state"}
    assert body["authorization_url"].startswith("https://www.facebook.com/v26.0/dialog/oauth?")
    assert signer.verify(body["state"]) == "demo"
    assert FAKE_APP_SECRET not in res.text


def test_start_connection_validates_body(client: TestClient) -> None:
    assert client.post(f"{BASE}/connections/start", json={}).status_code == 422
    assert client.post(f"{BASE}/connections/start", json={"tenant_id": ""}).status_code == 422


# ------------------------------------------------------------- callback ---
def test_callback_persists_and_returns_public_account(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    fake_repo: FakeConnectedAccountRepository,
) -> None:
    _stub_happy_path(graph_mock)
    res = _callback(client, signer)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["tenant_id"] == "demo"
    assert body["provider"] == "meta"
    assert body["provider_user_id"] == "10001"  # from /me, not /debug_token
    assert body["provider_user_name"] == "Nile Owner"
    assert body["status"] == "active"
    assert all(p["has_page_token"] is True for p in body["pages"])
    assert body["granted_scopes"] == ["pages_show_list", "instagram_basic"]
    assert body["expires_at"].startswith("2030-03-17")  # from debug_token expires_at
    assert "token_expires_at" not in body
    assert [p["page_id"] for p in body["pages"]] == ["1001", "1002"]
    assert body["pages"][0]["instagram"]["username"] == "nilecoffee"
    assert body["pages"][1]["instagram"] is None
    _assert_no_secrets(res.text)
    assert fake_repo.upsert_calls == 1


def test_callback_user_denied_400(client: TestClient) -> None:
    res = client.get(
        f"{BASE}/callback",
        params={"error": "access_denied", "error_description": "User denied the request"},
    )
    assert res.status_code == 400
    assert res.json()["detail"]["error"] == {
        "type": "user_denied",
        "message": "User denied the request",
    }


def test_callback_other_provider_error_400(client: TestClient) -> None:
    res = client.get(f"{BASE}/callback", params={"error": "server_error"})
    assert res.status_code == 400
    assert res.json()["detail"]["error"]["type"] == "provider_error"
    assert res.json()["detail"]["error"]["message"] == "server_error"


def test_callback_missing_parameters_400(client: TestClient) -> None:
    assert client.get(f"{BASE}/callback").status_code == 400
    res = client.get(f"{BASE}/callback", params={"code": "x"})
    assert res.json()["detail"]["error"]["type"] == "missing_parameters"


def test_complete_connection_invalid_state_400(
    client: TestClient, graph_mock: respx.MockRouter
) -> None:
    route = graph_mock.get("/oauth/access_token").mock(return_value=httpx.Response(200, json={}))
    res = client.get(f"{BASE}/callback", params={"code": "CODE", "state": "forged.state"})
    assert res.status_code == 400
    assert res.json()["detail"]["error"]["type"] == "invalid_oauth_state"
    assert route.call_count == 0


def test_callback_expired_state_400(client: TestClient) -> None:
    old = OAuthStateSigner(FAKE_STATE_SECRET, clock=lambda: 1_000_000.0).issue("demo")
    res = client.get(f"{BASE}/callback", params={"code": "CODE", "state": old})
    assert res.status_code == 400
    assert res.json()["detail"]["error"]["type"] == "invalid_oauth_state"


def test_callback_invalid_code_returns_401(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner
) -> None:
    graph_mock.get("/oauth/access_token").mock(
        return_value=httpx.Response(
            400, json=oauth_error_payload("Invalid verification code", code=100, subcode=None)
        )
    )
    res = _callback(client, signer)
    assert res.status_code == 401
    detail = res.json()["detail"]
    assert detail["error"] == "MetaOAuthError"
    assert detail["required_action"] == "reconnect"


def test_callback_expired_token_returns_401(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner
) -> None:
    _stub_oauth(graph_mock)
    graph_mock.get("/me", params__contains={"fields": "id,name"}).mock(
        return_value=httpx.Response(200, json=me_payload())
    )
    graph_mock.get("/debug_token").mock(
        return_value=httpx.Response(401, json=oauth_error_payload())
    )
    res = _callback(client, signer)
    assert res.status_code == 401
    assert res.json()["detail"]["error"] == "MetaTokenExpiredError"
    assert res.json()["detail"]["required_action"] == "reconnect"


def test_callback_rate_limited_returns_429_with_retry_after(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner
) -> None:
    _stub_oauth(graph_mock)
    graph_mock.get("/me", params__contains={"fields": "id,name"}).mock(
        return_value=httpx.Response(200, json=me_payload())
    )
    graph_mock.get("/debug_token").mock(
        return_value=httpx.Response(200, json=debug_token_payload())
    )
    graph_mock.get("/me/accounts").mock(
        return_value=httpx.Response(
            429,
            json={"error": {"message": "limit", "code": 4}},
            headers={"Retry-After": "12"},
        )
    )
    res = _callback(client, signer)
    assert res.status_code == 429
    assert res.headers["Retry-After"] == "12"
    assert res.json()["detail"]["error"] == "MetaRateLimitError"
    assert res.json()["detail"]["required_action"] == "retry_later"


def test_callback_rate_limited_without_retry_after(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner
) -> None:
    _stub_oauth(graph_mock)
    graph_mock.get("/me", params__contains={"fields": "id,name"}).mock(
        return_value=httpx.Response(200, json=me_payload())
    )
    graph_mock.get("/debug_token").mock(
        return_value=httpx.Response(200, json=debug_token_payload())
    )
    graph_mock.get("/me/accounts").mock(
        return_value=httpx.Response(429, json={"error": {"message": "limit", "code": 4}})
    )
    res = _callback(client, signer)
    assert res.status_code == 429
    assert "Retry-After" not in res.headers


def test_callback_permission_error_returns_403(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner
) -> None:
    _stub_oauth(graph_mock)
    graph_mock.get("/me", params__contains={"fields": "id,name"}).mock(
        return_value=httpx.Response(200, json=me_payload())
    )
    graph_mock.get("/debug_token").mock(
        return_value=httpx.Response(200, json=debug_token_payload())
    )
    graph_mock.get("/me/accounts").mock(
        return_value=httpx.Response(
            403,
            json={"error": {"message": "(#200) missing", "code": 200, "type": "OAuthException"}},
        )
    )
    res = _callback(client, signer)
    assert res.status_code == 403
    assert res.json()["detail"]["error"] == "MetaPermissionError"
    assert res.json()["detail"]["required_action"] == "reauthorize_with_additional_scopes"


def test_callback_upstream_failure_returns_502(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner
) -> None:
    graph_mock.get("/oauth/access_token").mock(return_value=httpx.Response(500, text="oops"))
    res = _callback(client, signer)
    assert res.status_code == 502
    assert res.json()["detail"]["error"] == "MetaTransientError"


def test_callback_network_failure_returns_502(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner
) -> None:
    graph_mock.get("/oauth/access_token").mock(side_effect=httpx.ConnectError("refused"))
    res = _callback(client, signer)
    assert res.status_code == 502
    assert res.json()["detail"]["error"] == "MetaNetworkError"


def test_callback_unknown_graph_error_returns_400(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner
) -> None:
    graph_mock.get("/oauth/access_token").mock(
        return_value=httpx.Response(
            400,
            json={
                "error": {"message": "Unsupported", "code": 100, "type": "GraphMethodException"}
            },
        )
    )
    res = _callback(client, signer)
    assert res.status_code == 400
    assert res.json()["detail"]["error"] == "MetaGraphError"
    assert res.json()["detail"]["required_action"] is None


# ---------------------------------------------------------------- reads ---
def test_list_get_delete_flow(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner
) -> None:
    _stub_happy_path(graph_mock)
    created = _callback(client, signer).json()
    cid = created["id"]

    listed = client.get(f"{BASE}/connections", params={"tenant_id": "demo"})
    assert listed.status_code == 200
    assert [a["id"] for a in listed.json()] == [cid]

    assert client.get(f"{BASE}/connections", params={"tenant_id": "other"}).json() == []

    one = client.get(f"{BASE}/connections/{cid}", params={"tenant_id": "demo"})
    assert one.status_code == 200
    assert one.json()["id"] == cid

    other_get = client.get(f"{BASE}/connections/{cid}", params={"tenant_id": "other"})
    assert other_get.status_code == 404
    other_del = client.delete(f"{BASE}/connections/{cid}", params={"tenant_id": "other"})
    assert other_del.status_code == 404

    deleted = client.delete(f"{BASE}/connections/{cid}", params={"tenant_id": "demo"})
    assert deleted.status_code == 204
    assert deleted.content == b""
    gone = client.get(f"{BASE}/connections/{cid}", params={"tenant_id": "demo"})
    assert gone.status_code == 404


def test_get_unknown_connection_404(client: TestClient) -> None:
    res = client.get(f"{BASE}/connections/{uuid4()}", params={"tenant_id": "demo"})
    assert res.status_code == 404


def test_tenant_id_is_required_on_read_routes(client: TestClient) -> None:
    assert client.get(f"{BASE}/connections").status_code == 422
    assert client.get(f"{BASE}/connections/{uuid4()}").status_code == 422
    assert client.delete(f"{BASE}/connections/{uuid4()}").status_code == 422
    bad = client.get(f"{BASE}/connections/not-a-uuid", params={"tenant_id": "d"})
    assert bad.status_code == 422


def test_router_responses_never_contain_tokens(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner
) -> None:
    """U17 — scan every successful route body for anything token-like."""
    _stub_happy_path(graph_mock)
    bodies = [
        client.post(f"{BASE}/connections/start", json={"tenant_id": "demo"}).text,
        _callback(client, signer).text,
        client.get(f"{BASE}/connections", params={"tenant_id": "demo"}).text,
    ]
    cid = json.loads(bodies[2])[0]["id"]
    bodies.append(client.get(f"{BASE}/connections/{cid}", params={"tenant_id": "demo"}).text)
    for body in bodies:
        _assert_no_secrets(body)


# ------------------------------------------------- misconfiguration (503) --
def test_missing_encryption_key_returns_503(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, signer: OAuthStateSigner
) -> None:
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    no_key_settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert no_key_settings.token_encryption_key is None
    app.dependency_overrides[get_settings] = lambda: no_key_settings
    try:
        with TestClient(app) as c:
            res = _callback(c, signer)
        assert res.status_code == 503
        assert "encryption" in res.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()


def test_missing_state_secret_returns_503(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OAUTH_STATE_SECRET", raising=False)
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)  # type: ignore[call-arg]
    try:
        with TestClient(app) as c:
            res = c.post(f"{BASE}/connections/start", json={"tenant_id": "demo"})
        assert res.status_code == 503
        assert "state" in res.json()["detail"].lower()
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
        (MetaGraphError("unmapped", code=100), 400, None),
    ],
)
def test_required_action_in_error_detail(
    exc: MetaGraphError, expected_status: int, expected_action: str | None
) -> None:
    http_exc = _to_http(exc)
    assert http_exc.status_code == expected_status
    assert http_exc.detail["required_action"] == expected_action  # type: ignore[index]
