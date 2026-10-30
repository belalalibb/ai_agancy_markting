"""Contract tests — brand-scoped Meta connector routes + app wiring (SPRINT-03 B9).

SPRINT-02 error-mapping scenarios (401/403/429/502/400/503) are kept on ``GET /callback``;
SPRINT-03 adds the tenancy gate (B-U6/B-U7), page selection (B-U8/B-U9/B-U10), the B7
logging guard (B-U11), the B6 invariant (B-U12) and the extended Zero-Secret guard (B-U13).
"""

from __future__ import annotations

import json
import logging
import re
from uuid import UUID, uuid4

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
from src.modules.tenancy.domain.models import GrantRole

from tests.conftest import (
    AGENCY_ID,
    BRAND2_ID,
    BRAND_ID,
    FAKE_APP_SECRET,
    FAKE_STATE_SECRET,
    OWNER_ID,
    STRANGER_ID,
    debug_token_payload,
    grant,
    me_payload,
    oauth_error_payload,
    pages_payload,
    token_payload,
)
from tests.fakes import FakeConnectedAccountRepository, FakeStore

pytestmark = pytest.mark.contract

API = "/api/v1"
CALLBACK = f"{API}/social/meta/callback"


def brand_base(brand_id: UUID = BRAND_ID) -> str:
    return f"{API}/brands/{brand_id}/social/meta"


BASE = brand_base()

# ``EAA`` (Meta token prefix) is matched case-sensitively: with IGNORECASE a random UUID such
# as ``7a69deaa-…`` produced a false positive (flaky CI run on SPRINT-03 PR #7).
SECRET_PATTERNS = re.compile(
    r"(?<!has_page_)token|encrypted|\*\*\*|(?-i:EAA)|LONG_LIVED|PAGE_TOKEN_", re.IGNORECASE
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


def _stub_happy_path(graph_mock: respx.MockRouter, pages: int = 2) -> respx.Route:
    _stub_oauth(graph_mock)
    me_route = graph_mock.get("/me", params__contains={"fields": "id,name"}).mock(
        return_value=httpx.Response(200, json=me_payload())
    )
    graph_mock.get("/debug_token").mock(
        return_value=httpx.Response(200, json=debug_token_payload())
    )
    graph_mock.get("/me/accounts").mock(
        return_value=httpx.Response(200, json=pages_payload(pages))
    )
    return me_route


def _callback(
    client: TestClient,
    signer: OAuthStateSigner,
    tenant: UUID = OWNER_ID,
    brand: UUID = BRAND_ID,
) -> httpx.Response:
    params = {"code": "CODE", "state": signer.issue(tenant, brand)}
    return client.get(CALLBACK, params=params)


def _select(
    client: TestClient,
    cid: str,
    page_ids: list[str],
    tenant: UUID = OWNER_ID,
    brand: UUID = BRAND_ID,
) -> httpx.Response:
    return client.put(
        f"{brand_base(brand)}/connections/{cid}/pages/selection",
        json={"tenant_id": str(tenant), "page_ids": page_ids},
    )


def _assert_no_secrets(body: str) -> None:
    assert not SECRET_PATTERNS.search(body), body


def _pages_by_id(body: dict) -> dict[str, dict]:
    return {p["page_id"]: p for p in body["pages"]}


# ------------------------------------------------------------------ app ---
def test_health(client: TestClient) -> None:
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    assert res.json()["env"] == "test"


def test_swagger_docs_served(client: TestClient) -> None:
    res = client.get("/docs")
    assert res.status_code == 200
    assert "swagger" in res.text.lower()


def test_redoc_served(client: TestClient) -> None:
    assert client.get("/redoc").status_code == 200


def test_openapi_lists_exactly_the_brand_scoped_meta_routes(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    b = f"{API}/brands/{{brand_id}}/social/meta"
    expected = {
        f"{b}/connections/start",
        CALLBACK,
        f"{b}/connections",
        f"{b}/connections/{{connection_id}}",
        f"{b}/connections/{{connection_id}}/pages/selection",
    }
    meta_paths = {p for p in paths if "/social/meta" in p}
    assert meta_paths == expected
    assert set(paths[f"{b}/connections/{{connection_id}}"]) == {"get", "delete"}
    assert set(paths[f"{b}/connections/{{connection_id}}/pages/selection"]) == {"put"}
    # §4: no /social/meta/connections outside /brands/{brand_id}/; callback stays put.
    assert not any(p.startswith(f"{API}/social/meta/connections") for p in paths)


def test_removed_routes_return_404(client: TestClient) -> None:
    old = f"{API}/social/meta"
    assert client.post(f"{old}/connections/start", json={}).status_code in (404, 405)
    assert client.get(f"{old}/connections", params={"tenant_id": str(OWNER_ID)}).status_code == 404
    assert client.get(f"{old}/connections/{uuid4()}").status_code == 404
    assert client.get(f"{old}/authorize-url").status_code in (404, 405)


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
    res = client.post(f"{BASE}/connections/start", json={"tenant_id": str(OWNER_ID)})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["authorization_url"].startswith("https://www.facebook.com/")
    assert body["state"] in body["authorization_url"]
    # B-U7: the state carries both tenant and brand.
    assert signer.verify(body["state"]) == (OWNER_ID, BRAND_ID)
    assert FAKE_APP_SECRET not in res.text


def test_start_connection_validates_body(client: TestClient) -> None:
    assert client.post(f"{BASE}/connections/start", json={}).status_code == 422
    assert client.post(f"{BASE}/connections/start", json={"tenant_id": ""}).status_code == 422
    assert client.post(f"{BASE}/connections/start", json={"tenant_id": "demo"}).status_code == 422


def test_start_connection_unknown_brand_404(client: TestClient) -> None:
    res = client.post(
        f"{brand_base(uuid4())}/connections/start", json={"tenant_id": str(OWNER_ID)}
    )
    assert res.status_code == 404
    assert res.json()["detail"]["error"] == "brand_not_found"


def test_start_connection_stranger_404_not_403(client: TestClient) -> None:
    res = client.post(f"{BASE}/connections/start", json={"tenant_id": str(STRANGER_ID)})
    assert res.status_code == 404  # existence hidden


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
    assert body["brand_id"] == str(BRAND_ID)
    assert body["connected_by_tenant_id"] == str(OWNER_ID)
    assert body["provider"] == "meta"
    assert body["provider_user_id"] == "10001"
    assert body["provider_user_name"] == "Nile Owner"
    assert body["status"] == "active"
    assert len(body["pages"]) == 2
    assert fake_repo.upsert_calls == 1
    _assert_no_secrets(res.text)
    fake_repo.assert_selection_invariant()


def test_callback_user_denied_400(client: TestClient) -> None:
    res = client.get(
        CALLBACK,
        params={"error": "access_denied", "error_description": "User denied"},
    )
    assert res.status_code == 400
    assert res.json()["detail"]["error"]["type"] == "user_denied"


def test_callback_other_provider_error_400(client: TestClient) -> None:
    res = client.get(CALLBACK, params={"error": "server_error"})
    assert res.status_code == 400
    assert res.json()["detail"]["error"]["type"] == "provider_error"


def test_callback_missing_parameters_400(client: TestClient) -> None:
    assert client.get(CALLBACK).status_code == 400
    res = client.get(CALLBACK, params={"code": "x"})
    assert res.json()["detail"]["error"]["type"] == "missing_parameters"


def test_complete_connection_invalid_state_400(
    client: TestClient, fake_repo: FakeConnectedAccountRepository
) -> None:
    res = client.get(CALLBACK, params={"code": "CODE", "state": "forged.state"})
    assert res.status_code == 400
    assert res.json()["detail"]["error"]["type"] == "invalid_oauth_state"
    assert fake_repo.upsert_calls == 0


def test_callback_expired_state_400(client: TestClient) -> None:
    old = OAuthStateSigner(FAKE_STATE_SECRET, ttl_seconds=1, clock=lambda: 1_000.0).issue(
        OWNER_ID, BRAND_ID
    )
    res = client.get(CALLBACK, params={"code": "CODE", "state": old})
    assert res.status_code == 400
    assert "expired" in res.json()["detail"]["error"]["message"].lower()


def test_callback_invalid_code_returns_401(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner
) -> None:
    graph_mock.get("/oauth/access_token").mock(
        return_value=httpx.Response(
            400, json=oauth_error_payload("Invalid verification code", 100, None)
        )
    )
    res = _callback(client, signer)
    assert res.status_code == 401
    assert res.json()["detail"]["error"] == "MetaOAuthError"
    assert res.json()["detail"]["required_action"] == "reconnect"


def test_callback_expired_token_returns_401(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner
) -> None:
    _stub_oauth(graph_mock)
    graph_mock.get("/me", params__contains={"fields": "id,name"}).mock(
        return_value=httpx.Response(200, json=me_payload())
    )
    graph_mock.get("/debug_token").mock(
        return_value=httpx.Response(400, json=oauth_error_payload())
    )
    res = _callback(client, signer)
    assert res.status_code == 401
    assert res.json()["detail"]["error"] == "MetaTokenExpiredError"
    assert res.json()["detail"]["code"] == 190


def test_callback_rate_limited_returns_429_with_retry_after(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner
) -> None:
    graph_mock.get("/oauth/access_token").mock(
        return_value=httpx.Response(
            429,
            headers={"Retry-After": "7"},
            json={
                "error": {
                    "message": "Application request limit reached",
                    "type": "OAuthException",
                    "code": 4,
                }
            },
        )
    )
    res = _callback(client, signer)
    assert res.status_code == 429
    assert res.headers["retry-after"] == "7"
    assert res.json()["detail"]["required_action"] == "retry_later"


def test_callback_rate_limited_without_retry_after(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner
) -> None:
    graph_mock.get("/oauth/access_token").mock(
        return_value=httpx.Response(
            400,
            json={"error": {"message": "User request limit reached", "code": 17}},
        )
    )
    res = _callback(client, signer)
    assert res.status_code == 429
    assert "retry-after" not in res.headers


def test_callback_permission_error_returns_403(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner
) -> None:
    _stub_oauth(graph_mock)
    graph_mock.get("/me", params__contains={"fields": "id,name"}).mock(
        return_value=httpx.Response(
            400,
            json={
                "error": {
                    "message": "(#200) Requires pages_show_list",
                    "type": "OAuthException",
                    "code": 200,
                }
            },
        )
    )
    res = _callback(client, signer)
    assert res.status_code == 403
    assert res.json()["detail"]["required_action"] == "reauthorize_with_additional_scopes"


def test_callback_upstream_failure_returns_502(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner
) -> None:
    graph_mock.get("/oauth/access_token").mock(return_value=httpx.Response(503, json={}))
    res = _callback(client, signer)
    assert res.status_code == 502
    assert res.json()["detail"]["error"] == "MetaTransientError"


def test_callback_network_failure_returns_502(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner
) -> None:
    graph_mock.get("/oauth/access_token").mock(side_effect=httpx.ConnectError("boom"))
    res = _callback(client, signer)
    assert res.status_code == 502
    assert res.json()["detail"]["error"] == "MetaNetworkError"


def test_callback_unknown_graph_error_returns_400(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner
) -> None:
    graph_mock.get("/oauth/access_token").mock(
        return_value=httpx.Response(
            400,
            json={"error": {"message": "Unsupported request", "type": "GraphMethodException"}},
        )
    )
    res = _callback(client, signer)
    assert res.status_code == 400
    assert res.json()["detail"]["error"] == "MetaGraphError"


# ---------------------------------------------------- B-U7: tenancy gate ---
def test_callback_with_revoked_grant_404_without_graph_call(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    store: FakeStore,
    fake_repo: FakeConnectedAccountRepository,
) -> None:
    g = grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    start = client.post(f"{BASE}/connections/start", json={"tenant_id": str(AGENCY_ID)})
    assert start.status_code == 200
    state = start.json()["state"]
    # Grant revoked between start and callback.
    store.grants[g.id] = g.model_copy(update={"revoked_at": g.granted_at})

    me_route = _stub_happy_path(graph_mock)
    token_route = graph_mock.get("/oauth/access_token")
    res = client.get(CALLBACK, params={"code": "CODE", "state": state})
    assert res.status_code == 404
    assert res.json()["detail"]["error"] == "brand_not_found"
    assert me_route.call_count == 0
    assert token_route.call_count == 0
    assert fake_repo.rows == {}


def test_callback_with_viewer_grant_403_without_graph_call(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    store: FakeStore,
    fake_repo: FakeConnectedAccountRepository,
) -> None:
    grant(store, BRAND_ID, AGENCY_ID, GrantRole.VIEWER)
    me_route = _stub_happy_path(graph_mock)
    res = _callback(client, signer, tenant=AGENCY_ID)
    assert res.status_code == 403
    assert res.json()["detail"]["error"] == "insufficient_role"
    assert me_route.call_count == 0
    assert fake_repo.rows == {}


# -------------------------------------------- B-U6: viewer role matrix ---
def test_viewer_can_read_but_not_write(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    store: FakeStore,
) -> None:
    grant(store, BRAND_ID, AGENCY_ID, GrantRole.VIEWER)
    _stub_happy_path(graph_mock, pages=3)
    cid = _callback(client, signer).json()["id"]
    viewer = {"tenant_id": str(AGENCY_ID)}

    assert client.get(f"{BASE}/connections", params=viewer).status_code == 200
    assert client.get(f"{BASE}/connections/{cid}", params=viewer).status_code == 200
    start = client.post(f"{BASE}/connections/start", json=viewer)
    assert start.status_code == 403
    assert start.json()["detail"]["error"] == "insufficient_role"
    assert _select(client, cid, ["1001"], tenant=AGENCY_ID).status_code == 403
    assert client.delete(f"{BASE}/connections/{cid}", params=viewer).status_code == 403
    # Still there.
    assert client.get(f"{BASE}/connections/{cid}", params=viewer).status_code == 200


def test_manager_can_write(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner, store: FakeStore
) -> None:
    grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    _stub_happy_path(graph_mock, pages=3)
    res = _callback(client, signer, tenant=AGENCY_ID)
    assert res.status_code == 201
    assert res.json()["connected_by_tenant_id"] == str(AGENCY_ID)
    cid = res.json()["id"]
    assert _select(client, cid, ["1002"], tenant=AGENCY_ID).status_code == 200
    assert (
        client.delete(
            f"{BASE}/connections/{cid}", params={"tenant_id": str(AGENCY_ID)}
        ).status_code
        == 204
    )


# --------------------------------------------- B-U8: callback selection ---
def test_callback_two_pages_stored_unselected_without_tokens(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    fake_repo: FakeConnectedAccountRepository,
) -> None:
    _stub_happy_path(graph_mock, pages=2)
    body = _callback(client, signer).json()
    pages = _pages_by_id(body)
    assert set(pages) == {"1001", "1002"}
    for p in pages.values():
        assert p["is_selected"] is False
        assert p["has_page_token"] is False
    # IG metadata is kept (not a secret).
    assert pages["1001"]["instagram"]["ig_user_id"] == "17841400000001"
    assert pages["1001"]["instagram"]["username"] == "nilecoffee"
    stored = next(iter(fake_repo.rows.values()))
    assert all(p.encrypted_page_token is None for p in stored.pages)
    fake_repo.assert_selection_invariant()


def test_callback_single_page_stored_selected_with_token(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    fake_repo: FakeConnectedAccountRepository,
) -> None:
    _stub_happy_path(graph_mock, pages=1)
    body = _callback(client, signer).json()
    assert len(body["pages"]) == 1
    assert body["pages"][0]["is_selected"] is True
    assert body["pages"][0]["has_page_token"] is True
    stored = next(iter(fake_repo.rows.values()))
    assert stored.pages[0].encrypted_page_token is not None
    assert stored.pages[0].encrypted_page_token != "PAGE_TOKEN_1001"
    fake_repo.assert_selection_invariant()


def test_callback_zero_pages(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner
) -> None:
    _stub_happy_path(graph_mock, pages=0)
    body = _callback(client, signer).json()
    assert body["pages"] == []


# -------------------------------------- owner note 2: reconnect preserves ---
def test_reconnect_preserves_selection_and_refreshes_tokens(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    fake_repo: FakeConnectedAccountRepository,
) -> None:
    _stub_happy_path(graph_mock, pages=3)
    cid = _callback(client, signer).json()["id"]
    assert _select(client, cid, ["1001", "1003"]).status_code == 200
    before = fake_repo.rows[UUID(cid)]
    old_tokens = {p.page_id: p.encrypted_page_token for p in before.pages}
    old_user_token = before.encrypted_user_token

    # Token renewal: the user reconnects the same Meta account to the same brand.
    res = _callback(client, signer)
    assert res.status_code == 201
    assert res.json()["id"] == cid  # same row (ON CONFLICT update)
    pages = _pages_by_id(res.json())
    assert pages["1001"]["is_selected"] is True and pages["1001"]["has_page_token"] is True
    assert pages["1003"]["is_selected"] is True and pages["1003"]["has_page_token"] is True
    assert pages["1002"]["is_selected"] is False and pages["1002"]["has_page_token"] is False

    after = fake_repo.rows[UUID(cid)]
    new_tokens = {p.page_id: p.encrypted_page_token for p in after.pages}
    # Fresh ciphertexts (Fernet is randomised) — tokens were re-encrypted, not copied.
    assert new_tokens["1001"] is not None and new_tokens["1001"] != old_tokens["1001"]
    assert new_tokens["1003"] is not None and new_tokens["1003"] != old_tokens["1003"]
    assert new_tokens["1002"] is None
    assert after.encrypted_user_token != old_user_token
    assert after.connected_at == before.connected_at
    assert len(fake_repo.rows) == 1
    fake_repo.assert_selection_invariant()


def test_reconnect_with_two_pages_and_no_prior_selection_stays_unselected(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    fake_repo: FakeConnectedAccountRepository,
) -> None:
    _stub_happy_path(graph_mock, pages=2)
    first = _callback(client, signer).json()
    second = _callback(client, signer).json()
    assert first["id"] == second["id"]
    assert all(not p["is_selected"] and not p["has_page_token"] for p in second["pages"])
    fake_repo.assert_selection_invariant()


# -------------------------------------------------- B-U9: page selection ---
def test_selection_one_of_three(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    fake_repo: FakeConnectedAccountRepository,
) -> None:
    _stub_happy_path(graph_mock, pages=3)
    cid = _callback(client, signer).json()["id"]
    res = _select(client, cid, ["1002"])
    assert res.status_code == 200, res.text
    pages = _pages_by_id(res.json())
    assert pages["1002"] == {**pages["1002"], "is_selected": True, "has_page_token": True}
    for pid in ("1001", "1003"):
        assert pages[pid]["is_selected"] is False
        assert pages[pid]["has_page_token"] is False
    stored = fake_repo.rows[UUID(cid)]
    by_id = {p.page_id: p for p in stored.pages}
    assert by_id["1002"].encrypted_page_token not in (None, "PAGE_TOKEN_1002")
    assert by_id["1001"].encrypted_page_token is None
    assert by_id["1003"].encrypted_page_token is None
    _assert_no_secrets(res.text)
    fake_repo.assert_selection_invariant()


def test_deselect_wipes_token(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    fake_repo: FakeConnectedAccountRepository,
) -> None:
    _stub_happy_path(graph_mock, pages=3)
    cid = _callback(client, signer).json()["id"]
    assert _select(client, cid, ["1001", "1002"]).status_code == 200
    res = _select(client, cid, ["1002"])
    pages = _pages_by_id(res.json())
    assert pages["1001"]["is_selected"] is False and pages["1001"]["has_page_token"] is False
    assert pages["1002"]["is_selected"] is True and pages["1002"]["has_page_token"] is True
    by_id = {p.page_id: p for p in fake_repo.rows[UUID(cid)].pages}
    assert by_id["1001"].encrypted_page_token is None
    fake_repo.assert_selection_invariant()


def test_selection_unknown_page_ids_422_no_change(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    fake_repo: FakeConnectedAccountRepository,
) -> None:
    _stub_happy_path(graph_mock, pages=3)
    cid = _callback(client, signer).json()["id"]
    assert _select(client, cid, ["1001"]).status_code == 200
    snapshot = fake_repo.rows[UUID(cid)].model_copy(deep=True)

    res = _select(client, cid, ["1002", "9999", "8888"])
    assert res.status_code == 422
    assert res.json()["detail"] == {"error": "unknown_page_ids", "page_ids": ["9999", "8888"]}
    assert fake_repo.rows[UUID(cid)] == snapshot  # untouched
    fake_repo.assert_selection_invariant()


def test_selection_new_meta_pages_added_unselected(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    fake_repo: FakeConnectedAccountRepository,
) -> None:
    _stub_happy_path(graph_mock, pages=2)
    cid = _callback(client, signer).json()["id"]
    # A third Page appears on Meta before the selection call.
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, json=pages_payload(3)))
    res = _select(client, cid, ["1001"])
    pages = _pages_by_id(res.json())
    assert set(pages) == {"1001", "1002", "1003"}
    assert pages["1003"]["is_selected"] is False and pages["1003"]["has_page_token"] is False
    fake_repo.assert_selection_invariant()


def test_selection_expired_user_token_401_and_reauth_required(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    fake_repo: FakeConnectedAccountRepository,
) -> None:
    _stub_happy_path(graph_mock, pages=3)
    cid = _callback(client, signer).json()["id"]
    graph_mock.get("/me/accounts").mock(
        return_value=httpx.Response(400, json=oauth_error_payload())
    )
    res = _select(client, cid, ["1001"])
    assert res.status_code == 401
    assert res.json()["detail"]["error"] == "MetaTokenExpiredError"
    assert res.json()["detail"]["required_action"] == "reconnect"
    assert fake_repo.rows[UUID(cid)].status.value == "reauth_required"
    got = client.get(f"{BASE}/connections/{cid}", params={"tenant_id": str(OWNER_ID)})
    assert got.json()["status"] == "reauth_required"
    fake_repo.assert_selection_invariant()


def test_selection_validation(client: TestClient) -> None:
    cid = uuid4()
    url = f"{BASE}/connections/{cid}/pages/selection"
    t = str(OWNER_ID)
    assert client.put(url, json={"tenant_id": t, "page_ids": []}).status_code == 422
    assert client.put(url, json={"tenant_id": t, "page_ids": ["1", "1"]}).status_code == 422
    assert client.put(url, json={"tenant_id": t, "page_ids": [" "]}).status_code == 422
    assert client.put(url, json={"tenant_id": t, "page_ids": ["x"] * 51}).status_code == 422
    assert client.put(url, json={"page_ids": ["1"]}).status_code == 422
    # Valid body but unknown connection in an accessible brand → 404.
    assert client.put(url, json={"tenant_id": t, "page_ids": ["1"]}).status_code == 404


# ----------------------------------------- B-U10: two brands, one account ---
def test_same_meta_user_in_two_brands_is_two_independent_rows(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    fake_repo: FakeConnectedAccountRepository,
) -> None:
    _stub_happy_path(graph_mock, pages=3)
    a = _callback(client, signer, brand=BRAND_ID).json()
    b = _callback(client, signer, brand=BRAND2_ID).json()
    assert a["id"] != b["id"]
    assert a["provider_user_id"] == b["provider_user_id"] == "10001"
    assert len(fake_repo.rows) == 2

    assert _select(client, a["id"], ["1001"], brand=BRAND_ID).status_code == 200
    other = client.get(
        f"{brand_base(BRAND2_ID)}/connections/{b['id']}", params={"tenant_id": str(OWNER_ID)}
    ).json()
    assert all(not p["is_selected"] for p in other["pages"])
    # Cross-brand access to a connection id is a 404.
    cross = client.get(f"{BASE}/connections/{b['id']}", params={"tenant_id": str(OWNER_ID)})
    assert cross.status_code == 404
    fake_repo.assert_selection_invariant()


# ----------------------------------------------------- list / get / delete ---
def test_list_get_delete_flow(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner
) -> None:
    _stub_happy_path(graph_mock)
    created = _callback(client, signer).json()
    cid = created["id"]
    owner = {"tenant_id": str(OWNER_ID)}

    listed = client.get(f"{BASE}/connections", params=owner)
    assert listed.status_code == 200
    assert [c["id"] for c in listed.json()] == [cid]
    # Other brand of the same owner: empty.
    assert client.get(f"{brand_base(BRAND2_ID)}/connections", params=owner).json() == []

    one = client.get(f"{BASE}/connections/{cid}", params=owner)
    assert one.status_code == 200
    assert one.json()["id"] == cid

    stranger = {"tenant_id": str(STRANGER_ID)}
    assert client.get(f"{BASE}/connections/{cid}", params=stranger).status_code == 404
    assert client.delete(f"{BASE}/connections/{cid}", params=stranger).status_code == 404

    deleted = client.delete(f"{BASE}/connections/{cid}", params=owner)
    assert deleted.status_code == 204
    assert client.get(f"{BASE}/connections/{cid}", params=owner).status_code == 404
    assert client.delete(f"{BASE}/connections/{cid}", params=owner).status_code == 404


def test_get_unknown_connection_404(client: TestClient) -> None:
    res = client.get(f"{BASE}/connections/{uuid4()}", params={"tenant_id": str(OWNER_ID)})
    assert res.status_code == 404


def test_tenant_id_is_required_and_uuid_on_read_routes(client: TestClient) -> None:
    assert client.get(f"{BASE}/connections").status_code == 422
    assert client.get(f"{BASE}/connections/{uuid4()}").status_code == 422
    assert client.delete(f"{BASE}/connections/{uuid4()}").status_code == 422
    assert client.get(f"{BASE}/connections", params={"tenant_id": "demo"}).status_code == 422
    bad = client.get(f"{BASE}/connections/not-a-uuid", params={"tenant_id": str(OWNER_ID)})
    assert bad.status_code == 422


# ------------------------------------------------------- Zero-Secret guard ---
def test_router_responses_never_contain_tokens(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner
) -> None:
    """U17 — scan every successful route body for anything token-like."""
    _stub_happy_path(graph_mock, pages=3)
    owner = {"tenant_id": str(OWNER_ID)}
    bodies = [
        client.post(f"{BASE}/connections/start", json=owner).text,
        _callback(client, signer).text,
        client.get(f"{BASE}/connections", params=owner).text,
    ]
    cid = json.loads(bodies[2])[0]["id"]
    bodies.append(_select(client, cid, ["1001", "1002"]).text)
    bodies.append(client.get(f"{BASE}/connections/{cid}", params=owner).text)
    for body in bodies:
        _assert_no_secrets(body)


def test_openapi_has_no_token_named_fields_except_has_page_token(client: TestClient) -> None:
    """B-U13 — extended Zero-Secret guard over the *whole* OpenAPI document."""
    spec = client.get("/openapi.json").json()

    def walk(node: object, path: str) -> list[str]:
        hits: list[str] = []
        if isinstance(node, dict):
            for key, value in node.items():
                if "token" in key.lower() and key != "has_page_token":
                    hits.append(f"{path}/{key}")
                hits.extend(walk(value, f"{path}/{key}"))
        elif isinstance(node, list):
            for i, item in enumerate(node):
                hits.extend(walk(item, f"{path}[{i}]"))
        return hits

    offenders = [
        h
        for h in walk(spec.get("components", {}).get("schemas", {}), "schemas")
        if h.rsplit("/", 1)[-1] not in {"required", "properties", "title", "description"}
    ]
    assert offenders == [], offenders
    # And no path leaks a token-like segment either.
    assert not any("token" in p.lower() for p in spec["paths"])


# --------------------------------------------------- B7 / B-U11: logging ---
def test_identity_and_secrets_never_logged(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    fake_repo: FakeConnectedAccountRepository,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """B7 — the full flow at DEBUG emits neither identity nor tokens (raw or encrypted)."""
    _stub_happy_path(graph_mock, pages=3)
    owner = {"tenant_id": str(OWNER_ID)}
    with caplog.at_level(logging.DEBUG, logger="src"):
        client.post(f"{BASE}/connections/start", json=owner)
        created = _callback(client, signer).json()
        cid = created["id"]
        _select(client, cid, ["1001"])
        stored = fake_repo.rows[UUID(cid)].model_copy(deep=True)
        client.get(f"{BASE}/connections/{cid}", params=owner)
        client.delete(f"{BASE}/connections/{cid}", params=owner)

    text = "\n".join(
        f"{r.getMessage()} {r.args!r}" for r in caplog.records if r.name.startswith("src")
    )
    assert "Nile Owner" not in text
    assert "10001" not in text
    for raw in ("LONG_LIVED", "SHORT_LIVED", "PAGE_TOKEN_1001", "PAGE_TOKEN_1002"):
        assert raw not in text
    assert stored.encrypted_user_token not in text
    for p in stored.pages:
        if p.encrypted_page_token:
            assert p.encrypted_page_token not in text
    # Sanity: the flow did log *something* at the module level, so the guard is meaningful.
    assert any(r.name.startswith("src.modules.social_connect") for r in caplog.records)


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
        app.dependency_overrides.pop(get_settings, None)


def test_missing_state_secret_returns_503(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OAUTH_STATE_SECRET", raising=False)
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)  # type: ignore[call-arg]
    try:
        with TestClient(app) as c:
            res = c.post(f"{BASE}/connections/start", json={"tenant_id": str(OWNER_ID)})
        assert res.status_code == 503
        assert "state" in res.json()["detail"].lower()
    finally:
        app.dependency_overrides.pop(get_settings, None)


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
