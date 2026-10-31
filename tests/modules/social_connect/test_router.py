"""Contract tests — brand-scoped Meta connector routes + app wiring (SPRINT-03 B9).

SPRINT-02 error-mapping scenarios (401/403/429/502/400/503) are kept on ``GET /callback``;
SPRINT-03 adds the tenancy gate (B-U6/B-U7), page selection (B-U8/B-U9/B-U10), the B7
logging guard (B-U11), the B6 invariant (B-U12) and the extended Zero-Secret guard (B-U13).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.core.config import Settings, get_settings
from src.core.oauth_state import OAuthStateSigner
from src.core.security import TokenEncryptionService
from src.main import app as module_level_app
from src.modules.social_connect.domain.models import AccountStatus
from src.modules.social_connect.exceptions import (
    MetaGraphError,
    MetaNetworkError,
    MetaOAuthError,
    MetaPermissionError,
    MetaRateLimitError,
    MetaTokenExpiredError,
    MetaTransientError,
    ReauthRequiredError,
)
from src.modules.social_connect.meta_client import POST_FIELDS
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
from tests.fakes import FakeConnectedAccountRepository, FakeStatusMarker, FakeStore

pytestmark = pytest.mark.contract

API = "/api/v1"
CALLBACK = f"{API}/social/meta/callback"


def brand_base(brand_id: UUID = BRAND_ID) -> str:
    return f"{API}/brands/{brand_id}/social/meta"


BASE = brand_base()

# ``EAA`` (Meta token prefix) is matched case-sensitively: with IGNORECASE a random UUID such
# as ``7a69deaa-…`` produced a false positive (flaky CI run on SPRINT-03 PR #7).
SECRET_PATTERNS = re.compile(
    r"(?<!has_page_)token(?!_health)|encrypted|\*\*\*|(?-i:EAA)|LONG_LIVED|PAGE_TOKEN_",
    re.IGNORECASE,
)


Headers = Callable[[UUID], dict[str, str]]


@pytest.fixture
def signer() -> OAuthStateSigner:
    return OAuthStateSigner(FAKE_STATE_SECRET)


@pytest.fixture
def as_(auth_headers: Headers) -> Headers:
    """``as_(tenant_id)`` ⇒ Bearer headers for that tenant's user (SPRINT-04 B5)."""
    return auth_headers


def _stub_oauth(graph_mock: respx.MockRouter) -> None:
    graph_mock.post(
        "/oauth/access_token", data__contains={"grant_type": "fb_exchange_token"}
    ).mock(return_value=httpx.Response(200, json=token_payload("LONG_LIVED", 5183944)))
    graph_mock.post("/oauth/access_token").mock(
        return_value=httpx.Response(200, json=token_payload("SHORT_LIVED", 5000))
    )


def _stub_happy_path(graph_mock: respx.MockRouter, pages: int = 2) -> respx.Route:
    _stub_oauth(graph_mock)
    me_route = graph_mock.get("/me", params__contains={"fields": "id,name"}).mock(
        return_value=httpx.Response(200, json=me_payload())
    )
    graph_mock.post("/debug_token").mock(
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
    as_: Headers,
    cid: str,
    page_ids: list[str],
    tenant: UUID = OWNER_ID,
    brand: UUID = BRAND_ID,
) -> httpx.Response:
    return client.put(
        f"{brand_base(brand)}/connections/{cid}/pages/selection",
        json={"page_ids": page_ids},
        headers=as_(tenant),
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
        # SPRINT-05 B2/B3/B4
        f"{b}/connections/{{connection_id}}/resync",
        f"{b}/connections/{{connection_id}}/pages/{{page_id}}/posts",
        f"{b}/connections/{{connection_id}}/pages/{{page_id}}/instagram/media",
    }
    meta_paths = {p for p in paths if "/social/meta" in p}
    assert meta_paths == expected
    assert set(paths[f"{b}/connections/{{connection_id}}"]) == {"get", "delete"}
    assert set(paths[f"{b}/connections/{{connection_id}}/pages/selection"]) == {"put"}
    assert set(paths[f"{b}/connections/{{connection_id}}/resync"]) == {"post"}
    assert set(paths[f"{b}/connections/{{connection_id}}/pages/{{page_id}}/posts"]) == {"get"}
    # §4: no /social/meta/connections outside /brands/{brand_id}/; callback stays put.
    assert not any(p.startswith(f"{API}/social/meta/connections") for p in paths)


def test_removed_routes_return_404(client: TestClient, as_: Headers) -> None:
    old = f"{API}/social/meta"
    assert client.post(f"{old}/connections/start", json={}).status_code in (404, 405)
    assert client.get(f"{old}/connections", headers=as_(OWNER_ID)).status_code == 404
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
    client: TestClient, signer: OAuthStateSigner, as_: Headers
) -> None:
    res = client.post(f"{BASE}/connections/start", headers=as_(OWNER_ID))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["authorization_url"].startswith("https://www.facebook.com/")
    assert body["state"] in body["authorization_url"]
    # B-U7: the state carries both tenant and brand — taken from the JWT, not the body.
    assert signer.verify(body["state"]) == (OWNER_ID, BRAND_ID)
    assert FAKE_APP_SECRET not in res.text


def test_start_connection_state_carries_user_claim_u(
    client: TestClient, signer: OAuthStateSigner, as_: Headers
) -> None:
    """B-U4 — ``u`` = the user who started the flow (audit); callback needs no Bearer."""
    from tests.conftest import OWNER_USER_ID

    state = client.post(f"{BASE}/connections/start", headers=as_(OWNER_ID)).json()["state"]
    payload = signer.decode_payload(state)
    assert payload["t"] == OWNER_ID and payload["b"] == BRAND_ID
    assert payload["u"] == OWNER_USER_ID


def test_start_connection_validates_body(client: TestClient, as_: Headers) -> None:
    """B-U2 — the body is optional and must be *empty*; ``tenant_id`` is no longer accepted."""
    assert (
        client.post(f"{BASE}/connections/start", json={}, headers=as_(OWNER_ID)).status_code == 200
    )
    assert client.post(f"{BASE}/connections/start", headers=as_(OWNER_ID)).status_code == 200
    spoof = client.post(
        f"{BASE}/connections/start", json={"tenant_id": str(OWNER_ID)}, headers=as_(AGENCY_ID)
    )
    assert spoof.status_code == 422
    assert client.post(f"{BASE}/connections/start", json={}).status_code == 401


def test_start_connection_unknown_brand_404(client: TestClient, as_: Headers) -> None:
    res = client.post(f"{brand_base(uuid4())}/connections/start", headers=as_(OWNER_ID))
    assert res.status_code == 404
    assert res.json()["detail"]["error"] == "brand_not_found"


def test_start_connection_stranger_404_not_403(client: TestClient, as_: Headers) -> None:
    """B-U3 — a tenant without any grant sees 404 (existence hidden), never 403."""
    res = client.post(f"{BASE}/connections/start", headers=as_(STRANGER_ID))
    assert res.status_code == 404


def test_start_connection_agency_state_names_agency(
    client: TestClient, signer: OAuthStateSigner, store: FakeStore, as_: Headers
) -> None:
    """B-U3 — a MANAGER grantee starting the flow gets ``state.t == AGENCY_ID``."""
    grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    res = client.post(f"{BASE}/connections/start", headers=as_(AGENCY_ID))
    assert res.status_code == 200
    assert signer.verify(res.json()["state"]) == (AGENCY_ID, BRAND_ID)


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
    graph_mock.post("/oauth/access_token").mock(
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
    graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(400, json=oauth_error_payload())
    )
    res = _callback(client, signer)
    assert res.status_code == 401
    assert res.json()["detail"]["error"] == "MetaTokenExpiredError"
    assert res.json()["detail"]["code"] == 190


def test_callback_rate_limited_returns_429_with_retry_after(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner
) -> None:
    graph_mock.post("/oauth/access_token").mock(
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
    graph_mock.post("/oauth/access_token").mock(
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
    assert res.json()["detail"]["required_action"] == "reconnect"  # SPRINT-06 A-2: unified


def test_callback_upstream_failure_returns_502(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner
) -> None:
    graph_mock.post("/oauth/access_token").mock(return_value=httpx.Response(503, json={}))
    res = _callback(client, signer)
    assert res.status_code == 502
    assert res.json()["detail"]["error"] == "upstream_unavailable"  # A-2: fixed 502 everywhere


def test_callback_network_failure_returns_502(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner
) -> None:
    graph_mock.post("/oauth/access_token").mock(side_effect=httpx.ConnectError("boom"))
    res = _callback(client, signer)
    assert res.status_code == 502
    assert res.json()["detail"]["error"] == "upstream_unavailable"  # A-2: fixed 502 everywhere


def test_callback_unknown_graph_error_returns_400(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner
) -> None:
    graph_mock.post("/oauth/access_token").mock(
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
    as_: Headers,
) -> None:
    g = grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    start = client.post(f"{BASE}/connections/start", headers=as_(AGENCY_ID))
    assert start.status_code == 200
    state = start.json()["state"]
    # Grant revoked between start and callback.
    store.grants[g.id] = g.model_copy(update={"revoked_at": g.granted_at})

    me_route = _stub_happy_path(graph_mock)
    token_route = graph_mock.post("/oauth/access_token")
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
    as_: Headers,
) -> None:
    grant(store, BRAND_ID, AGENCY_ID, GrantRole.VIEWER)
    _stub_happy_path(graph_mock, pages=3)
    cid = _callback(client, signer).json()["id"]
    viewer = as_(AGENCY_ID)

    assert client.get(f"{BASE}/connections", headers=viewer).status_code == 200
    assert client.get(f"{BASE}/connections/{cid}", headers=viewer).status_code == 200
    start = client.post(f"{BASE}/connections/start", headers=viewer)
    assert start.status_code == 403
    assert start.json()["detail"]["error"] == "insufficient_role"
    assert _select(client, as_, cid, ["1001"], tenant=AGENCY_ID).status_code == 403
    assert client.delete(f"{BASE}/connections/{cid}", headers=viewer).status_code == 403
    # Still there.
    assert client.get(f"{BASE}/connections/{cid}", headers=viewer).status_code == 200


def test_manager_can_write(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    store: FakeStore,
    as_: Headers,
) -> None:
    grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    _stub_happy_path(graph_mock, pages=3)
    res = _callback(client, signer, tenant=AGENCY_ID)
    assert res.status_code == 201
    assert res.json()["connected_by_tenant_id"] == str(AGENCY_ID)
    cid = res.json()["id"]
    assert _select(client, as_, cid, ["1002"], tenant=AGENCY_ID).status_code == 200
    assert client.delete(f"{BASE}/connections/{cid}", headers=as_(AGENCY_ID)).status_code == 204


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
    as_: Headers,
) -> None:
    _stub_happy_path(graph_mock, pages=3)
    cid = _callback(client, signer).json()["id"]
    assert _select(client, as_, cid, ["1001", "1003"]).status_code == 200
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
    as_: Headers,
) -> None:
    _stub_happy_path(graph_mock, pages=3)
    cid = _callback(client, signer).json()["id"]
    res = _select(client, as_, cid, ["1002"])
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
    as_: Headers,
) -> None:
    _stub_happy_path(graph_mock, pages=3)
    cid = _callback(client, signer).json()["id"]
    assert _select(client, as_, cid, ["1001", "1002"]).status_code == 200
    res = _select(client, as_, cid, ["1002"])
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
    as_: Headers,
) -> None:
    _stub_happy_path(graph_mock, pages=3)
    cid = _callback(client, signer).json()["id"]
    assert _select(client, as_, cid, ["1001"]).status_code == 200
    snapshot = fake_repo.rows[UUID(cid)].model_copy(deep=True)

    res = _select(client, as_, cid, ["1002", "9999", "8888"])
    assert res.status_code == 422
    assert res.json()["detail"] == {"error": "unknown_page_ids", "page_ids": ["9999", "8888"]}
    assert fake_repo.rows[UUID(cid)] == snapshot  # untouched
    fake_repo.assert_selection_invariant()


def test_selection_new_meta_pages_added_unselected(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    fake_repo: FakeConnectedAccountRepository,
    as_: Headers,
) -> None:
    _stub_happy_path(graph_mock, pages=2)
    cid = _callback(client, signer).json()["id"]
    # A third Page appears on Meta before the selection call.
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, json=pages_payload(3)))
    res = _select(client, as_, cid, ["1001"])
    pages = _pages_by_id(res.json())
    assert set(pages) == {"1001", "1002", "1003"}
    assert pages["1003"]["is_selected"] is False and pages["1003"]["has_page_token"] is False
    fake_repo.assert_selection_invariant()


def test_selection_expired_user_token_401_and_reauth_required(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    fake_repo: FakeConnectedAccountRepository,
    as_: Headers,
) -> None:
    _stub_happy_path(graph_mock, pages=3)
    cid = _callback(client, signer).json()["id"]
    graph_mock.get("/me/accounts").mock(
        return_value=httpx.Response(400, json=oauth_error_payload())
    )
    res = _select(client, as_, cid, ["1001"])
    assert res.status_code == 401
    assert res.json()["detail"]["error"] == "MetaTokenExpiredError"
    assert res.json()["detail"]["required_action"] == "reconnect"
    assert fake_repo.rows[UUID(cid)].status.value == "reauth_required"
    got = client.get(f"{BASE}/connections/{cid}", headers=as_(OWNER_ID))
    assert got.json()["status"] == "reauth_required"
    fake_repo.assert_selection_invariant()


def test_selection_validation(client: TestClient, as_: Headers) -> None:
    cid = uuid4()
    url = f"{BASE}/connections/{cid}/pages/selection"
    h = as_(OWNER_ID)
    assert client.put(url, json={"page_ids": []}, headers=h).status_code == 422
    assert client.put(url, json={"page_ids": ["1", "1"]}, headers=h).status_code == 422
    assert client.put(url, json={"page_ids": [" "]}, headers=h).status_code == 422
    assert client.put(url, json={"page_ids": ["x"] * 51}, headers=h).status_code == 422
    # B-U2: ``tenant_id`` in the body is rejected (extra=forbid).
    assert (
        client.put(
            url, json={"tenant_id": str(OWNER_ID), "page_ids": ["1"]}, headers=h
        ).status_code
        == 422
    )
    assert client.put(url, json={"page_ids": ["1"]}).status_code == 401
    # Valid body but unknown connection in an accessible brand → 404.
    assert client.put(url, json={"page_ids": ["1"]}, headers=h).status_code == 404


# ----------------------------------------- B-U10: two brands, one account ---
def test_same_meta_user_in_two_brands_is_two_independent_rows(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    fake_repo: FakeConnectedAccountRepository,
    as_: Headers,
) -> None:
    _stub_happy_path(graph_mock, pages=3)
    a = _callback(client, signer, brand=BRAND_ID).json()
    b = _callback(client, signer, brand=BRAND2_ID).json()
    assert a["id"] != b["id"]
    assert a["provider_user_id"] == b["provider_user_id"] == "10001"
    assert len(fake_repo.rows) == 2

    assert _select(client, as_, a["id"], ["1001"], brand=BRAND_ID).status_code == 200
    other = client.get(
        f"{brand_base(BRAND2_ID)}/connections/{b['id']}", headers=as_(OWNER_ID)
    ).json()
    assert all(not p["is_selected"] for p in other["pages"])
    # Cross-brand access to a connection id is a 404.
    cross = client.get(f"{BASE}/connections/{b['id']}", headers=as_(OWNER_ID))
    assert cross.status_code == 404
    fake_repo.assert_selection_invariant()


# ----------------------------------------------------- list / get / delete ---
def test_list_get_delete_flow(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    as_: Headers,
) -> None:
    _stub_happy_path(graph_mock)
    created = _callback(client, signer).json()
    cid = created["id"]
    owner = as_(OWNER_ID)

    listed = client.get(f"{BASE}/connections", headers=owner)
    assert listed.status_code == 200
    assert [c["id"] for c in listed.json()] == [cid]
    # Other brand of the same owner: empty.
    assert client.get(f"{brand_base(BRAND2_ID)}/connections", headers=owner).json() == []

    one = client.get(f"{BASE}/connections/{cid}", headers=owner)
    assert one.status_code == 200
    assert one.json()["id"] == cid

    stranger = as_(STRANGER_ID)
    assert client.get(f"{BASE}/connections/{cid}", headers=stranger).status_code == 404
    assert client.delete(f"{BASE}/connections/{cid}", headers=stranger).status_code == 404

    deleted = client.delete(f"{BASE}/connections/{cid}", headers=owner)
    assert deleted.status_code == 204
    assert client.get(f"{BASE}/connections/{cid}", headers=owner).status_code == 404
    assert client.delete(f"{BASE}/connections/{cid}", headers=owner).status_code == 404


def test_get_unknown_connection_404(client: TestClient, as_: Headers) -> None:
    res = client.get(f"{BASE}/connections/{uuid4()}", headers=as_(OWNER_ID))
    assert res.status_code == 404


def test_bearer_is_required_on_read_routes_and_query_tenant_id_is_ignored(
    client: TestClient, as_: Headers
) -> None:
    """SPRINT-04 B-U5 — no Bearer ⇒ 401; a stray ``?tenant_id=`` never changes the actor."""
    for res in (
        client.get(f"{BASE}/connections"),
        client.get(f"{BASE}/connections/{uuid4()}"),
        client.delete(f"{BASE}/connections/{uuid4()}"),
    ):
        assert res.status_code == 401
        assert res.headers["www-authenticate"] == "Bearer"
    # Stranger spoofing the owner via query string still gets the stranger's view (404).
    spoof = client.get(
        f"{BASE}/connections", params={"tenant_id": str(OWNER_ID)}, headers=as_(STRANGER_ID)
    )
    assert spoof.status_code == 404
    # And the owner with a bogus query value is unaffected.
    assert (
        client.get(
            f"{BASE}/connections", params={"tenant_id": "demo"}, headers=as_(OWNER_ID)
        ).status_code
        == 200
    )
    bad = client.get(f"{BASE}/connections/not-a-uuid", headers=as_(OWNER_ID))
    assert bad.status_code == 422


# ------------------------------------------------------- Zero-Secret guard ---
def test_router_responses_never_contain_tokens(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    as_: Headers,
) -> None:
    """U17 — scan every successful route body for anything token-like."""
    _stub_happy_path(graph_mock, pages=3)
    owner = as_(OWNER_ID)
    bodies = [
        client.post(f"{BASE}/connections/start", headers=owner).text,
        _callback(client, signer).text,
        client.get(f"{BASE}/connections", headers=owner).text,
    ]
    cid = json.loads(bodies[2])[0]["id"]
    bodies.append(_select(client, as_, cid, ["1001", "1002"]).text)
    bodies.append(client.get(f"{BASE}/connections/{cid}", headers=owner).text)
    for body in bodies:
        _assert_no_secrets(body)


def test_openapi_has_no_token_named_fields_except_has_page_token(client: TestClient) -> None:
    """B-U13 / SPRINT-04 A-U10 — extended Zero-Secret guard over every *response* schema.

    Forbidden key fragments: ``password``, ``hash``, ``secret``, ``token``. Allowed exceptions:
    ``access_token`` (the JWT itself), ``token_type`` (constant ``"bearer"``, mandated by A7)
    and ``has_page_token`` (boolean flag). Request bodies legitimately carry ``password`` and
    are therefore excluded by following only ``responses`` ``$ref``s.
    """
    spec = client.get("/openapi.json").json()
    schemas = spec.get("components", {}).get("schemas", {})
    forbidden = ("password", "hash", "secret", "token")
    # ``token_health`` (SPRINT-05 B1): derived closed-Literal health label, not a credential.
    # ``prompt_tokens`` / ``completion_tokens`` / ``prompt_hash`` (SPRINT-07 A-4): the explicit
    # ``GenerationLogEntry`` model — LLM usage counters and a sha256 digest of the prompt,
    # OWNER/MANAGER only (D-066); not credentials.
    allowed = {
        "access_token",
        "token_type",
        "has_page_token",
        "token_health",
        "prompt_tokens",
        "completion_tokens",
        "prompt_hash",
    }
    structural = {"required", "properties", "title", "description", "type", "format"}

    def refs(node: object) -> set[str]:
        out: set[str] = set()
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                out.add(ref.rsplit("/", 1)[-1])
            for value in node.values():
                out |= refs(value)
        elif isinstance(node, list):
            for item in node:
                out |= refs(item)
        return out

    # Response schemas = closure over $ref starting from every operation's ``responses``.
    reachable: set[str] = set()
    pending = {
        name
        for methods in spec["paths"].values()
        for op in methods.values()
        for name in refs(op.get("responses", {}))
    }
    while pending:
        name = pending.pop()
        if name in reachable or name not in schemas:
            continue
        reachable.add(name)
        pending |= refs(schemas[name])
    # Sanity: the guard really sees the auth + social schemas.
    assert {"TokenOut", "MeOut", "ConnectedAccountPublic"} <= reachable

    def walk(node: object, path: str) -> list[str]:
        hits: list[str] = []
        if isinstance(node, dict):
            for key, value in node.items():
                lowered = key.lower()
                if (
                    key not in structural
                    and key not in allowed
                    and any(fragment in lowered for fragment in forbidden)
                ):
                    hits.append(f"{path}/{key}")
                hits.extend(walk(value, f"{path}/{key}"))
        elif isinstance(node, list):
            for i, item in enumerate(node):
                hits.extend(walk(item, f"{path}[{i}]"))
        return hits

    offenders = [h for name in sorted(reachable) for h in walk(schemas[name], name)]
    assert offenders == [], offenders
    # Request side: password may appear, but never a hash or secret. SPRINT-09 B-1 adds one
    # more *write-only* credential intake — the BYOK Cloud API number (``WhatsAppAccountCreate``:
    # ``system_token`` / ``app_secret`` / ``verify_token``, ``repr=False``, encrypted at rest,
    # §2-0 ج). It is request-only: the response closure above still forbids these names in
    # every response schema, and ``WhatsAppAccountPublic`` is asserted to be reachable.
    byok_request_fields = {
        "WhatsAppAccountCreate/properties/system_token",
        "WhatsAppAccountCreate/properties/app_secret",
        "WhatsAppAccountCreate/properties/verify_token",
    }
    assert "WhatsAppAccountCreate" not in reachable
    assert "WhatsAppAccountPublic" in reachable
    request_hits = [
        h
        for name in sorted(set(schemas) - reachable)
        for h in walk(schemas[name], name)
        if "password" not in h.lower() and h not in byok_request_fields
    ]
    assert request_hits == [], request_hits
    # And no path leaks a token-like segment either.
    assert not any("token" in p.lower() for p in spec["paths"])


# --------------------------------------------------- B7 / B-U11: logging ---
def test_identity_and_secrets_never_logged(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    fake_repo: FakeConnectedAccountRepository,
    caplog: pytest.LogCaptureFixture,
    as_: Headers,
) -> None:
    """B7 — the full flow at DEBUG emits neither identity nor tokens (raw or encrypted)."""
    _stub_happy_path(graph_mock, pages=3)
    owner = as_(OWNER_ID)
    with caplog.at_level(logging.DEBUG, logger="src"):
        client.post(f"{BASE}/connections/start", headers=owner)
        created = _callback(client, signer).json()
        cid = created["id"]
        _select(client, as_, cid, ["1001"])
        stored = fake_repo.rows[UUID(cid)].model_copy(deep=True)
        client.get(f"{BASE}/connections/{cid}", headers=owner)
        client.delete(f"{BASE}/connections/{cid}", headers=owner)

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


def test_missing_state_secret_returns_503(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, as_: Headers
) -> None:
    headers = as_(OWNER_ID)  # issued with the *test* JWT secret before settings are swapped
    monkeypatch.delenv("OAUTH_STATE_SECRET", raising=False)
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)  # type: ignore[call-arg]
    try:
        with TestClient(app) as c:
            res = c.post(f"{BASE}/connections/start", headers=headers)
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
        (MetaPermissionError("(#200) missing", code=200), 403, "reconnect"),  # A-2 unified
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


# ======================================================= SPRINT-04.1 H1 / H3
def _agency_connection(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    store: FakeStore,
    role: GrantRole = GrantRole.MANAGER,
    pages: int = 3,
) -> str:
    """Agency (grantee) connects its Meta account — 3 Pages of *different* clients."""
    grant(store, BRAND_ID, AGENCY_ID, role)
    _stub_happy_path(graph_mock, pages=pages)
    res = _callback(client, signer, tenant=AGENCY_ID)
    assert res.status_code == 201, res.text
    assert res.json()["connected_by_tenant_id"] == str(AGENCY_ID)
    return res.json()["id"]


def _rearmed_pages_route(graph_mock: respx.MockRouter, pages: int = 3) -> respx.Route:
    """``/me/accounts`` stub with a zeroed call counter (``Route.reset`` drops the mock)."""
    route = graph_mock.get("/me/accounts")
    route.reset()
    return route.mock(return_value=httpx.Response(200, json=pages_payload(pages)))


def test_h1_u1_brand_owner_sees_only_selected_pages_of_agency_connection(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    store: FakeStore,
    as_: Headers,
) -> None:
    cid = _agency_connection(client, graph_mock, signer, store)
    owner = as_(OWNER_ID)

    # Before any selection: the owner sees no Pages at all (they are the agency's data).
    one = client.get(f"{BASE}/connections/{cid}", headers=owner)
    assert one.status_code == 200
    assert one.json()["pages"] == []
    assert one.json()["is_connection_owner"] is False
    listed = client.get(f"{BASE}/connections", headers=owner).json()
    assert listed[0]["pages"] == [] and listed[0]["is_connection_owner"] is False

    assert _select(client, as_, cid, ["1002"], tenant=AGENCY_ID).status_code == 200
    one = client.get(f"{BASE}/connections/{cid}", headers=owner).json()
    assert [p["page_id"] for p in one["pages"]] == ["1002"]
    assert "Giza Gym" not in json.dumps(one) and "Nile Coffee Roasters" not in json.dumps(one)


def test_h1_u2_agency_sees_all_pages_of_its_own_connection(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    store: FakeStore,
    as_: Headers,
) -> None:
    cid = _agency_connection(client, graph_mock, signer, store)
    agency = as_(AGENCY_ID)
    one = client.get(f"{BASE}/connections/{cid}", headers=agency).json()
    assert len(one["pages"]) == 3
    assert one["is_connection_owner"] is True
    assert _select(client, as_, cid, ["1002"], tenant=AGENCY_ID).status_code == 200
    one = client.get(f"{BASE}/connections/{cid}", headers=agency).json()
    assert len(one["pages"]) == 3  # still all three for the owner of the connection
    assert {p["page_id"]: p["is_selected"] for p in one["pages"]} == {
        "1001": False,
        "1002": True,
        "1003": False,
    }


def test_h1_u3_brand_owner_cannot_select_on_agency_connection(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    store: FakeStore,
    fake_repo: FakeConnectedAccountRepository,
    as_: Headers,
) -> None:
    cid = _agency_connection(client, graph_mock, signer, store)
    pages_route = _rearmed_pages_route(graph_mock)
    snapshot = fake_repo.rows[UUID(cid)].model_copy(deep=True)

    res = _select(client, as_, cid, ["1001"], tenant=OWNER_ID)
    assert res.status_code == 403, res.text
    assert res.json()["detail"]["error"] == "not_connection_owner"
    assert pages_route.call_count == 0  # refused before any Graph call
    assert fake_repo.rows[UUID(cid)] == snapshot
    fake_repo.assert_selection_invariant()


def test_h1_u4_agency_cannot_select_on_owner_connection(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    store: FakeStore,
    as_: Headers,
) -> None:
    grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    _stub_happy_path(graph_mock, pages=3)
    cid = _callback(client, signer, tenant=OWNER_ID).json()["id"]
    pages_route = _rearmed_pages_route(graph_mock)
    res = _select(client, as_, cid, ["1001"], tenant=AGENCY_ID)
    assert res.status_code == 403
    assert res.json()["detail"]["error"] == "not_connection_owner"
    assert pages_route.call_count == 0
    # The owner itself still can.
    assert _select(client, as_, cid, ["1001"], tenant=OWNER_ID).status_code == 200


def test_h1_u5_disconnect_policy_owner_may_evict_agency_manager_may_not_evict_owner(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    store: FakeStore,
    as_: Headers,
) -> None:
    grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    _stub_happy_path(graph_mock, pages=3)
    agency_cid = _callback(client, signer, tenant=AGENCY_ID).json()["id"]
    graph_mock.get("/me", params__contains={"fields": "id,name"}).mock(
        return_value=httpx.Response(200, json=me_payload(user_id="20002", name="Owner Self"))
    )
    graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(200, json=debug_token_payload(user_id="20002"))
    )
    owner_cid = _callback(client, signer, tenant=OWNER_ID).json()["id"]
    assert agency_cid != owner_cid

    # MANAGER (agency) tries to remove the brand owner's connection ⇒ 403, still there.
    res = client.delete(f"{BASE}/connections/{owner_cid}", headers=as_(AGENCY_ID))
    assert res.status_code == 403
    assert res.json()["detail"]["error"] == "not_connection_owner"
    assert client.get(f"{BASE}/connections/{owner_cid}", headers=as_(OWNER_ID)).status_code == 200

    # MANAGER removes its *own* connection ⇒ 204 … but first the OWNER evicts it: 204.
    res = client.delete(f"{BASE}/connections/{agency_cid}", headers=as_(OWNER_ID))
    assert res.status_code == 204
    assert client.get(f"{BASE}/connections/{agency_cid}", headers=as_(OWNER_ID)).status_code == 404

    # A manager can still delete a connection it made itself.
    agency_cid2 = _callback(client, signer, tenant=AGENCY_ID).json()["id"]
    assert (
        client.delete(f"{BASE}/connections/{agency_cid2}", headers=as_(AGENCY_ID)).status_code
        == 204
    )


def test_h1_u6_reconnect_by_a_different_tenant_transfers_ownership(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    store: FakeStore,
    fake_repo: FakeConnectedAccountRepository,
    as_: Headers,
    caplog: pytest.LogCaptureFixture,
) -> None:
    grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    _stub_happy_path(graph_mock, pages=3)
    cid = _callback(client, signer, tenant=AGENCY_ID).json()["id"]
    assert _select(client, as_, cid, ["1001"], tenant=AGENCY_ID).status_code == 200

    # The same Meta user (same provider_user_id) re-runs OAuth as the brand owner.
    with caplog.at_level(logging.INFO, logger="src.modules.social_connect.service"):
        res = _callback(client, signer, tenant=OWNER_ID)
    assert res.status_code == 201
    assert res.json()["id"] == cid
    assert res.json()["connected_by_tenant_id"] == str(OWNER_ID)
    assert res.json()["is_connection_owner"] is True
    assert fake_repo.rows[UUID(cid)].connected_by_tenant_id == OWNER_ID
    transfer = [r for r in caplog.records if "ownership transferred" in r.getMessage()]
    assert len(transfer) == 1
    assert str(AGENCY_ID) in transfer[0].getMessage() and str(OWNER_ID) in transfer[0].getMessage()
    assert "Nile Owner" not in caplog.text and "10001" not in caplog.text  # ids only (B7)

    # Ownership moved: the agency can no longer change the selection; the owner can.
    assert _select(client, as_, cid, ["1002"], tenant=AGENCY_ID).status_code == 403
    assert _select(client, as_, cid, ["1002"], tenant=OWNER_ID).status_code == 200


def test_h3_u1_page_without_token_is_not_selectable_422_no_change(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    fake_repo: FakeConnectedAccountRepository,
    as_: Headers,
) -> None:
    _stub_happy_path(graph_mock, pages=3)
    cid = _callback(client, signer).json()["id"]
    assert _select(client, as_, cid, ["1001"]).status_code == 200
    snapshot = fake_repo.rows[UUID(cid)].model_copy(deep=True)

    payload = pages_payload(3)
    del payload["data"][2]["access_token"]  # 1003: user has no Page role (F4)
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, json=payload))

    res = _select(client, as_, cid, ["1002", "1003"])
    assert res.status_code == 422, res.text
    assert res.json()["detail"] == {"error": "page_not_selectable", "page_ids": ["1003"]}
    assert fake_repo.rows[UUID(cid)] == snapshot  # untouched: 1001 still the only selection
    fake_repo.assert_selection_invariant()


def test_h3_expired_token_status_is_written_out_of_band(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    status_marker: FakeStatusMarker,
    as_: Headers,
) -> None:
    _stub_happy_path(graph_mock, pages=3)
    cid = _callback(client, signer).json()["id"]
    graph_mock.get("/me/accounts").mock(
        return_value=httpx.Response(400, json=oauth_error_payload())
    )
    assert _select(client, as_, cid, ["1001"]).status_code == 401
    assert status_marker.calls == [(BRAND_ID, UUID(cid), AccountStatus.REAUTH_REQUIRED)]


# ================================================================ SPRINT-05 Part B
RESYNC = "resync"


def _owner_connection_with_1001_selected(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner, as_: Headers
) -> str:
    """OWNER connects (2 Pages) and selects 1001 (has Instagram); 1002 stays unselected."""
    _stub_happy_path(graph_mock, pages=2)
    cid = _callback(client, signer).json()["id"]
    assert _select(client, as_, cid, ["1001"]).status_code == 200
    return cid


def _posts_payload(count: int = 2, *, with_next: bool = False) -> dict:
    data = [
        {
            "id": f"1001_{i}",
            "message": f"post {i}",
            "created_time": "2026-10-20T10:00:00+0000",
            "permalink_url": f"https://www.facebook.com/1001/posts/{i}",
            "status_type": "mobile_status_update",
            "full_picture": None,
            "is_published": True,
        }
        for i in range(count)
    ]
    paging: dict = {"cursors": {"before": "BEFORE", "after": "AFTER_CURSOR"}}
    if with_next:
        paging["next"] = (
            "https://graph.facebook.com/v26.0/1001/posts?access_token=PAGE_TOKEN_1001"
            "&limit=25&after=AFTER_CURSOR"
        )
    return {"data": data, "paging": paging}


def _media_payload(count: int = 2, *, with_next: bool = False) -> dict:
    data = [
        {
            "id": f"178_{i}",
            "caption": f"media {i}",
            "media_type": "IMAGE",
            "media_url": f"https://scontent.example/{i}.jpg",
            "thumbnail_url": None,
            "permalink": f"https://www.instagram.com/p/x{i}/",
            "timestamp": "2026-10-21T09:00:00+0000",
            "like_count": 3,
            "comments_count": 1,
        }
        for i in range(count)
    ]
    paging: dict = {"cursors": {"before": "B", "after": "IG_AFTER"}}
    if with_next:
        paging["next"] = (
            "https://graph.facebook.com/v26.0/17841400000001/media"
            "?access_token=PAGE_TOKEN_1001&limit=25&after=IG_AFTER"
        )
    return {"data": data, "paging": paging}


def _assert_bearer_only(request: httpx.Request, token: str) -> None:
    """§2-0(a): Bearer + ``appsecret_proof`` (recomputed), nothing secret in the URL."""
    # ``access_token`` may legitimately appear as a *field name* inside ``fields=`` (PAGE_FIELDS);
    # what must never exist is an ``access_token`` query *parameter* or the token value itself.
    assert "access_token" not in dict(request.url.params)
    assert token not in str(request.url)
    assert request.headers["authorization"].startswith("Bearer ")
    assert request.headers["authorization"] == f"Bearer {token}"
    expected = hmac.new(FAKE_APP_SECRET.encode(), token.encode(), hashlib.sha256).hexdigest()
    assert dict(request.url.params)["appsecret_proof"] == expected


def _posts_url(cid: str, page_id: str = "1001", brand: UUID = BRAND_ID) -> str:
    return f"{brand_base(brand)}/connections/{cid}/pages/{page_id}/posts"


def _media_url(cid: str, page_id: str = "1001", brand: UUID = BRAND_ID) -> str:
    return f"{brand_base(brand)}/connections/{cid}/pages/{page_id}/instagram/media"


def _get(client: TestClient, url: str, tenant: UUID, as_: Headers, **params: object) -> int:
    return client.get(url, params=params or None, headers=as_(tenant)).status_code


# ------------------------------------------------------------------ B2: resync
def test_b2_u1_resync_keeps_selection_and_replaces_page_token(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    as_: Headers,
    fake_repo: FakeConnectedAccountRepository,
    encryption: TokenEncryptionService,
) -> None:
    cid = _owner_connection_with_1001_selected(client, graph_mock, signer, as_)
    before = fake_repo.rows[UUID(cid)]
    old_cipher = {p.page_id: p.encrypted_page_token for p in before.pages}
    assert encryption.decrypt_token(old_cipher["1001"] or "") == "PAGE_TOKEN_1001"

    rotated = pages_payload(2)
    rotated["data"][0]["access_token"] = "PAGE_TOKEN_1001_ROTATED"  # noqa: S105
    pages_route = graph_mock.get("/me/accounts").mock(
        return_value=httpx.Response(200, json=rotated)
    )
    graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(
            200, json=debug_token_payload(scopes=["pages_show_list", "pages_read_engagement"])
        )
    )
    res = client.post(f"{BASE}/connections/{cid}/{RESYNC}", headers=as_(OWNER_ID))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["removed_selected_page_ids"] == []
    pages = _pages_by_id(body)
    assert pages["1001"]["is_selected"] is True and pages["1001"]["has_page_token"] is True
    assert pages["1002"]["is_selected"] is False and pages["1002"]["has_page_token"] is False
    assert body["granted_scopes"] == ["pages_show_list", "pages_read_engagement"]
    assert body["token_health"] == "valid"
    assert body["last_synced_at"] is not None
    _assert_no_secrets(res.text)

    after = fake_repo.rows[UUID(cid)]
    by_id = {p.page_id: p for p in after.pages}
    assert by_id["1001"].encrypted_page_token != old_cipher["1001"]
    assert encryption.decrypt_token(by_id["1001"].encrypted_page_token or "") == (
        "PAGE_TOKEN_1001_ROTATED"
    )
    assert by_id["1002"].encrypted_page_token is None
    assert after.encrypted_user_token == before.encrypted_user_token  # user token untouched
    assert after.status is AccountStatus.ACTIVE
    fake_repo.assert_selection_invariant()
    _assert_bearer_only(pages_route.calls[-1].request, "LONG_LIVED")


def test_b2_u2_resync_drops_vanished_selected_page_and_reports_it(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    as_: Headers,
    fake_repo: FakeConnectedAccountRepository,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _stub_happy_path(graph_mock, pages=3)
    cid = _callback(client, signer).json()["id"]
    assert _select(client, as_, cid, ["1001", "1003"]).status_code == 200

    # Meta now returns only 1001 + 1002: the selected 1003 vanished.
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, json=pages_payload(2)))
    with caplog.at_level(logging.WARNING):
        res = client.post(f"{BASE}/connections/{cid}/{RESYNC}", headers=as_(OWNER_ID))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["removed_selected_page_ids"] == ["1003"]
    pages = _pages_by_id(body)
    assert set(pages) == {"1001", "1002"}
    assert pages["1001"]["is_selected"] is True and pages["1001"]["has_page_token"] is True
    assert pages["1002"]["is_selected"] is False and pages["1002"]["has_page_token"] is False

    stored = fake_repo.rows[UUID(cid)]
    assert all(p.page_id != "1003" for p in stored.pages)  # no orphan token row
    assert sum(1 for p in stored.pages if p.encrypted_page_token) == 1
    fake_repo.assert_selection_invariant()
    warned = [r for r in caplog.records if "resync dropped selected pages" in r.getMessage()]
    assert len(warned) == 1 and "1003" in warned[0].getMessage()
    assert "PAGE_TOKEN" not in caplog.text and "LONG_LIVED" not in caplog.text


def test_b2_u3_resync_by_non_owner_is_403_without_graph_call(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    store: FakeStore,
    as_: Headers,
) -> None:
    """Brand OWNER who is not the connection owner ⇒ 403 ``not_connection_owner``; VIEWER ⇒
    403 ``insufficient_role``; no Graph request in either case."""
    cid = _agency_connection(client, graph_mock, signer, store)
    pages_route = _rearmed_pages_route(graph_mock)
    debug_route = graph_mock.post("/debug_token")
    debug_route.reset()
    debug_route.mock(return_value=httpx.Response(200, json=debug_token_payload()))

    res = client.post(f"{BASE}/connections/{cid}/{RESYNC}", headers=as_(OWNER_ID))
    assert res.status_code == 403, res.text
    assert res.json()["detail"]["error"] == "not_connection_owner"
    assert pages_route.called is False and debug_route.called is False

    grant(store, BRAND_ID, STRANGER_ID, GrantRole.VIEWER)
    viewer = client.post(f"{BASE}/connections/{cid}/{RESYNC}", headers=as_(STRANGER_ID))
    assert viewer.status_code == 403
    assert viewer.json()["detail"]["error"] == "insufficient_role"
    assert pages_route.called is False and debug_route.called is False


def test_b2_u4_resync_revoked_connection_is_409(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    as_: Headers,
    fake_repo: FakeConnectedAccountRepository,
) -> None:
    cid = _owner_connection_with_1001_selected(client, graph_mock, signer, as_)
    fake_repo.rows[UUID(cid)] = fake_repo.rows[UUID(cid)].model_copy(
        update={"status": AccountStatus.REVOKED}
    )
    pages_route = _rearmed_pages_route(graph_mock, pages=2)
    res = client.post(f"{BASE}/connections/{cid}/{RESYNC}", headers=as_(OWNER_ID))
    assert res.status_code == 409, res.text
    assert res.json()["detail"]["error"] == "connection_revoked"
    assert pages_route.called is False
    assert fake_repo.rows[UUID(cid)].status is AccountStatus.REVOKED  # not resurrected


def test_b2_resync_invalid_token_marks_reauth_required_and_401(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    as_: Headers,
    fake_repo: FakeConnectedAccountRepository,
    status_marker: FakeStatusMarker,
) -> None:
    cid = _owner_connection_with_1001_selected(client, graph_mock, signer, as_)
    graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(200, json=debug_token_payload(is_valid=False, error_code=190))
    )
    pages_route = _rearmed_pages_route(graph_mock, pages=2)
    res = client.post(f"{BASE}/connections/{cid}/{RESYNC}", headers=as_(OWNER_ID))
    assert res.status_code == 401, res.text
    assert res.json()["detail"]["required_action"] == "reconnect"
    assert pages_route.called is False
    assert status_marker.calls == [(BRAND_ID, UUID(cid), AccountStatus.REAUTH_REQUIRED)]
    assert fake_repo.rows[UUID(cid)].status is AccountStatus.REAUTH_REQUIRED
    # The selection survives — only a reconnect can refresh it.
    assert [p.page_id for p in fake_repo.rows[UUID(cid)].pages if p.is_selected] == ["1001"]


def test_b2_resync_unknown_connection_404_and_debug_degradation_keeps_old_metadata(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    as_: Headers,
    fake_repo: FakeConnectedAccountRepository,
) -> None:
    missing = client.post(f"{BASE}/connections/{uuid4()}/{RESYNC}", headers=as_(OWNER_ID))
    assert missing.status_code == 404
    cid = _owner_connection_with_1001_selected(client, graph_mock, signer, as_)
    before = fake_repo.rows[UUID(cid)]
    graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(
            500, json={"error": {"message": "down", "code": 2, "type": "ApiException"}}
        )
    )
    res = client.post(f"{BASE}/connections/{cid}/{RESYNC}", headers=as_(OWNER_ID))
    assert res.status_code == 200, res.text
    after = fake_repo.rows[UUID(cid)]
    assert after.granted_scopes == before.granted_scopes
    assert after.token_expires_at == before.token_expires_at
    assert after.last_synced_at is not None


def test_b2_resync_single_page_is_auto_selected(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    as_: Headers,
    fake_repo: FakeConnectedAccountRepository,
) -> None:
    """B6 inside B2: 2 Pages, none selected → Meta now returns 1 → auto-selected with token."""
    _stub_happy_path(graph_mock, pages=2)
    cid = _callback(client, signer).json()["id"]
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, json=pages_payload(1)))
    res = client.post(f"{BASE}/connections/{cid}/{RESYNC}", headers=as_(OWNER_ID))
    assert res.status_code == 200, res.text
    pages = _pages_by_id(res.json())
    assert set(pages) == {"1001"} and pages["1001"]["is_selected"] is True
    assert res.json()["removed_selected_page_ids"] == []
    fake_repo.assert_selection_invariant()


# ------------------------------------------------------------- B3: page posts
def test_b3_u1_posts_request_uses_bearer_page_token_and_appsecret_proof(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner, as_: Headers
) -> None:
    cid = _owner_connection_with_1001_selected(client, graph_mock, signer, as_)
    route = graph_mock.get("/1001/posts").mock(
        return_value=httpx.Response(200, json=_posts_payload(2))
    )
    res = client.get(_posts_url(cid), params={"limit": 10}, headers=as_(OWNER_ID))
    assert res.status_code == 200, res.text
    assert route.called
    req = route.calls[0].request
    assert req.method == "GET"
    _assert_bearer_only(req, "PAGE_TOKEN_1001")
    qs = dict(req.url.params)
    assert qs["fields"] == (
        "id,message,created_time,permalink_url,status_type,full_picture,is_published,"
        "attachments{media_type,type,url,media{image{src}}}"
    )
    assert qs["limit"] == "10"
    assert "after" not in qs
    body = res.json()
    assert [p["id"] for p in body["items"]] == ["1001_0", "1001_1"]
    assert body["items"][0]["message"] == "post 0"
    assert body["items"][0]["is_published"] is True
    assert body["has_more"] is False and body["next_cursor"] is None
    _assert_no_secrets(res.text)


def test_b3_u2_posts_pagination_exposes_cursor_only_never_paging_next(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    as_: Headers,
    caplog: pytest.LogCaptureFixture,
) -> None:
    cid = _owner_connection_with_1001_selected(client, graph_mock, signer, as_)
    route = graph_mock.get("/1001/posts").mock(
        return_value=httpx.Response(200, json=_posts_payload(2, with_next=True))
    )
    with caplog.at_level(logging.DEBUG):
        first = client.get(_posts_url(cid), headers=as_(OWNER_ID))
    assert first.status_code == 200
    body = first.json()
    assert body["next_cursor"] == "AFTER_CURSOR" and body["has_more"] is True
    assert "paging" not in body and "next" not in body
    assert "graph.facebook.com" not in first.text
    assert "access_token" not in first.text
    assert "PAGE_TOKEN_1001" not in caplog.text
    assert "v26.0/1001/posts" not in caplog.text
    assert route.call_count == 1  # never auto-followed

    second = client.get(_posts_url(cid), params={"after": "AFTER_CURSOR"}, headers=as_(OWNER_ID))
    assert second.status_code == 200
    assert dict(route.calls[1].request.url.params)["after"] == "AFTER_CURSOR"
    assert dict(route.calls[1].request.url.params)["limit"] == "25"  # default


def test_b3_u3_unselected_page_is_404_for_owner_and_everyone_else(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    store: FakeStore,
    as_: Headers,
) -> None:
    """§2-0(b): same 404 ``page_not_selected`` for the connection owner, a MANAGER, a VIEWER and
    for an unknown Page id; no Graph call."""
    cid = _owner_connection_with_1001_selected(client, graph_mock, signer, as_)
    grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    grant(store, BRAND_ID, STRANGER_ID, GrantRole.VIEWER)
    route = graph_mock.get("/1002/posts").mock(return_value=httpx.Response(200, json={}))
    bodies = []
    for tenant in (OWNER_ID, AGENCY_ID, STRANGER_ID):
        res = client.get(_posts_url(cid, "1002"), headers=as_(tenant))
        assert res.status_code == 404, res.text
        bodies.append(res.json())
    assert bodies[0] == bodies[1] == bodies[2]
    assert bodies[0]["detail"]["error"] == "page_not_selected"
    assert bodies[0]["detail"]["page_id"] == "1002"
    assert route.called is False
    ghost = client.get(_posts_url(cid, "9999"), headers=as_(OWNER_ID))
    assert ghost.status_code == 404 and ghost.json()["detail"]["error"] == "page_not_selected"
    assert _get(client, _posts_url(str(uuid4())), OWNER_ID, as_) == 404


def test_b3_u4_viewer_can_read_posts_of_selected_page(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    store: FakeStore,
    as_: Headers,
) -> None:
    cid = _owner_connection_with_1001_selected(client, graph_mock, signer, as_)
    grant(store, BRAND_ID, STRANGER_ID, GrantRole.VIEWER)
    graph_mock.get("/1001/posts").mock(return_value=httpx.Response(200, json=_posts_payload(1)))
    res = client.get(_posts_url(cid), headers=as_(STRANGER_ID))
    assert res.status_code == 200, res.text
    assert len(res.json()["items"]) == 1
    # No grant at all ⇒ 404 brand_not_found (B5), no Graph call.
    assert _get(client, _posts_url(cid, brand=BRAND2_ID), STRANGER_ID, as_) == 404


def test_b3_u5_reauth_required_connection_is_409_without_graph_call(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    as_: Headers,
    fake_repo: FakeConnectedAccountRepository,
) -> None:
    cid = _owner_connection_with_1001_selected(client, graph_mock, signer, as_)
    fake_repo.rows[UUID(cid)] = fake_repo.rows[UUID(cid)].model_copy(
        update={"status": AccountStatus.REAUTH_REQUIRED}
    )
    route = graph_mock.get("/1001/posts").mock(return_value=httpx.Response(200, json={}))
    res = client.get(_posts_url(cid), headers=as_(OWNER_ID))
    assert res.status_code == 409, res.text
    assert res.json()["detail"]["error"] == "reauth_required"
    assert res.json()["detail"]["required_action"] == "reconnect"
    assert route.called is False

    fake_repo.rows[UUID(cid)] = fake_repo.rows[UUID(cid)].model_copy(
        update={"status": AccountStatus.REVOKED}
    )
    revoked = client.get(_posts_url(cid), headers=as_(OWNER_ID))
    assert revoked.status_code == 409
    assert revoked.json()["detail"]["error"] == "connection_revoked"
    assert route.called is False


def test_b3_u6_190_during_read_marks_reauth_required_via_marker_and_401(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    as_: Headers,
    fake_repo: FakeConnectedAccountRepository,
    status_marker: FakeStatusMarker,
) -> None:
    cid = _owner_connection_with_1001_selected(client, graph_mock, signer, as_)
    graph_mock.get("/1001/posts").mock(
        return_value=httpx.Response(400, json=oauth_error_payload("expired", code=190))
    )
    res = client.get(_posts_url(cid), headers=as_(OWNER_ID))
    assert res.status_code == 401, res.text
    assert res.json()["detail"]["required_action"] == "reconnect"
    assert status_marker.calls == [(BRAND_ID, UUID(cid), AccountStatus.REAUTH_REQUIRED)]
    assert fake_repo.rows[UUID(cid)].status is AccountStatus.REAUTH_REQUIRED
    assert "PAGE_TOKEN" not in res.text and "LONG_LIVED" not in res.text


def test_b3_u7_limit_out_of_range_and_after_too_long_are_422(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner, as_: Headers
) -> None:
    cid = _owner_connection_with_1001_selected(client, graph_mock, signer, as_)
    route = graph_mock.get("/1001/posts").mock(return_value=httpx.Response(200, json={}))
    assert _get(client, _posts_url(cid), OWNER_ID, as_, limit=101) == 422
    assert _get(client, _posts_url(cid), OWNER_ID, as_, limit=0) == 422
    assert _get(client, _posts_url(cid), OWNER_ID, as_, after="x" * 513) == 422
    assert route.called is False
    ok = client.get(_posts_url(cid), params={"limit": 100}, headers=as_(OWNER_ID))
    assert ok.status_code == 200 and dict(route.calls[0].request.url.params)["limit"] == "100"


def test_b3_posts_provider_errors_map_to_429_403_502(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner, as_: Headers
) -> None:
    cid = _owner_connection_with_1001_selected(client, graph_mock, signer, as_)
    route = graph_mock.get("/1001/posts")
    route.mock(
        return_value=httpx.Response(
            429, json={"error": {"message": "slow down", "code": 4}}, headers={"Retry-After": "30"}
        )
    )
    res = client.get(_posts_url(cid), headers=as_(OWNER_ID))
    assert res.status_code == 429 and res.headers["Retry-After"] == "30"
    assert res.json()["detail"]["required_action"] == "retry_later"

    route.mock(
        return_value=httpx.Response(
            403, json={"error": {"message": "no perm", "code": 10, "type": "OAuthException"}}
        )
    )
    res = client.get(_posts_url(cid), headers=as_(OWNER_ID))
    assert res.status_code == 403 and res.json()["detail"]["required_action"] == "reconnect"

    route.mock(return_value=httpx.Response(500, json={"error": {"message": "x", "code": 2}}))
    res = client.get(_posts_url(cid), headers=as_(OWNER_ID))
    assert res.status_code == 502
    assert res.json()["detail"] == {
        "provider": "meta",
        "error": "upstream_unavailable",
        "required_action": None,
    }

    route.mock(side_effect=httpx.ConnectError("boom"))
    res = client.get(_posts_url(cid), headers=as_(OWNER_ID))
    assert res.status_code == 502 and "boom" not in res.text


# -------------------------------------------------------- B4: instagram media
def test_b4_u1_media_request_uses_page_token_against_ig_user_id(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner, as_: Headers
) -> None:
    cid = _owner_connection_with_1001_selected(client, graph_mock, signer, as_)
    route = graph_mock.get("/17841400000001/media").mock(
        return_value=httpx.Response(200, json=_media_payload(2))
    )
    res = client.get(_media_url(cid), headers=as_(OWNER_ID))
    assert res.status_code == 200, res.text
    req = route.calls[0].request
    assert req.method == "GET"
    _assert_bearer_only(req, "PAGE_TOKEN_1001")
    qs = dict(req.url.params)
    assert qs["fields"] == (
        "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,"
        "like_count,comments_count"
    )
    assert qs["limit"] == "25"
    body = res.json()
    assert [m["id"] for m in body["items"]] == ["178_0", "178_1"]
    assert body["items"][0]["media_type"] == "IMAGE"
    assert body["items"][0]["like_count"] == 3 and body["items"][0]["comments_count"] == 1
    assert body["has_more"] is False and body["next_cursor"] is None
    _assert_no_secrets(res.text)


def test_b4_u2_page_without_instagram_is_404_instagram_not_linked(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    as_: Headers,
    store: FakeStore,
) -> None:
    _stub_happy_path(graph_mock, pages=2)
    cid = _callback(client, signer).json()["id"]
    assert _select(client, as_, cid, ["1002"]).status_code == 200  # 1002 has no IG
    grant(store, BRAND_ID, STRANGER_ID, GrantRole.VIEWER)
    for tenant in (OWNER_ID, STRANGER_ID):
        res = client.get(_media_url(cid, "1002"), headers=as_(tenant))
        assert res.status_code == 404, res.text
        assert res.json()["detail"]["error"] == "instagram_not_linked"
    # Unselected Page with IG ⇒ still page_not_selected (selection check comes first).
    res = client.get(_media_url(cid, "1001"), headers=as_(OWNER_ID))
    assert res.status_code == 404 and res.json()["detail"]["error"] == "page_not_selected"


def test_b4_u3_media_pagination_cursor_only(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner, as_: Headers
) -> None:
    cid = _owner_connection_with_1001_selected(client, graph_mock, signer, as_)
    route = graph_mock.get("/17841400000001/media").mock(
        return_value=httpx.Response(200, json=_media_payload(1, with_next=True))
    )
    res = client.get(_media_url(cid), params={"limit": 1}, headers=as_(OWNER_ID))
    assert res.status_code == 200
    assert res.json()["next_cursor"] == "IG_AFTER" and res.json()["has_more"] is True
    assert "graph.facebook.com" not in res.text and "access_token" not in res.text
    assert route.call_count == 1
    nxt = client.get(_media_url(cid), params={"after": "IG_AFTER"}, headers=as_(OWNER_ID))
    assert nxt.status_code == 200
    assert dict(route.calls[1].request.url.params)["after"] == "IG_AFTER"


def test_b4_u4_permission_error_is_403_reconnect(
    client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner, as_: Headers
) -> None:
    cid = _owner_connection_with_1001_selected(client, graph_mock, signer, as_)
    graph_mock.get("/17841400000001/media").mock(
        return_value=httpx.Response(
            400,
            json={
                "error": {
                    "message": "(#200) Requires instagram_basic",
                    "code": 200,
                    "type": "OAuthException",
                }
            },
        )
    )
    res = client.get(_media_url(cid), headers=as_(OWNER_ID))
    assert res.status_code == 403, res.text
    assert res.json()["detail"]["required_action"] == "reconnect"
    assert res.json()["detail"]["code"] == 200


def test_b4_media_status_guards_and_190(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    as_: Headers,
    status_marker: FakeStatusMarker,
) -> None:
    cid = _owner_connection_with_1001_selected(client, graph_mock, signer, as_)
    route = graph_mock.get("/17841400000001/media").mock(
        return_value=httpx.Response(400, json=oauth_error_payload("expired", code=190))
    )
    res = client.get(_media_url(cid), headers=as_(OWNER_ID))
    assert res.status_code == 401
    assert status_marker.calls[-1][2] is AccountStatus.REAUTH_REQUIRED
    assert _get(client, _media_url(cid), OWNER_ID, as_) == 409
    assert route.call_count == 1
    assert _get(client, _media_url(str(uuid4())), OWNER_ID, as_) == 404
    assert _get(client, _media_url(cid), OWNER_ID, as_, limit=101) == 422


# ================================================================ SPRINT-06 Part A
# A1-U1: debug_token answers ``is_valid=true, scopes=[]`` ⇒ the empty list IS persisted
# (empty ≠ unknown) and ``scopes_verified`` flips to true.
def test_a1_u1_resync_persists_empty_verified_scope_list(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    as_: Headers,
    fake_repo: FakeConnectedAccountRepository,
) -> None:
    cid = _owner_connection_with_1001_selected(client, graph_mock, signer, as_)
    assert fake_repo.rows[UUID(cid)].granted_scopes != []
    graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(200, json=debug_token_payload(scopes=[]))
    )
    _rearmed_pages_route(graph_mock, pages=2)
    res = client.post(f"{BASE}/connections/{cid}/{RESYNC}", headers=as_(OWNER_ID))
    assert res.status_code == 200, res.text
    assert res.json()["granted_scopes"] == []
    assert res.json()["scopes_verified"] is True
    after = fake_repo.rows[UUID(cid)]
    assert after.granted_scopes == []
    assert after.scopes_verified is True


# A1-U2: a transient debug_token failure keeps the stored scopes *and* the verified flag.
def test_a1_u2_resync_debug_token_failure_keeps_old_scopes(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    as_: Headers,
    fake_repo: FakeConnectedAccountRepository,
) -> None:
    cid = _owner_connection_with_1001_selected(client, graph_mock, signer, as_)
    before = fake_repo.rows[UUID(cid)]
    assert before.scopes_verified is True and before.granted_scopes
    graph_mock.post("/debug_token").mock(side_effect=httpx.ConnectTimeout("slow"))
    _rearmed_pages_route(graph_mock, pages=2)
    res = client.post(f"{BASE}/connections/{cid}/{RESYNC}", headers=as_(OWNER_ID))
    assert res.status_code == 200, res.text
    after = fake_repo.rows[UUID(cid)]
    assert after.granted_scopes == before.granted_scopes
    assert after.scopes_verified is True
    assert res.json()["granted_scopes"] == before.granted_scopes


# A1-U3: ``scopes_verified`` is exposed with both values — true after a verified callback,
# false when the callback had to degrade (debug_token unreachable ⇒ scopes unknown).
@pytest.mark.parametrize(
    ("debug_response", "expected_verified"),
    [
        (httpx.Response(200, json=debug_token_payload()), True),
        (httpx.Response(500, json={"error": {"message": "down", "code": 2}}), False),
    ],
    ids=["verified", "unverified"],
)
def test_a1_u3_scopes_verified_exposed_with_both_values(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    as_: Headers,
    debug_response: httpx.Response,
    expected_verified: bool,
) -> None:
    _stub_happy_path(graph_mock, pages=2)
    graph_mock.post("/debug_token").mock(return_value=debug_response)
    created = _callback(client, signer)
    assert created.status_code == 201, created.text
    assert created.json()["scopes_verified"] is expected_verified
    assert (created.json()["granted_scopes"] != []) is expected_verified
    listed = client.get(f"{BASE}/connections", headers=as_(OWNER_ID)).json()
    assert listed[0]["scopes_verified"] is expected_verified
    _assert_no_secrets(created.text)


# A1-U4: a ``reauth_required`` connection ⇒ 409 ``reauth_required`` with **no** Graph call.
def test_a1_u4_resync_reauth_required_is_409_without_graph_call(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    as_: Headers,
    fake_repo: FakeConnectedAccountRepository,
) -> None:
    cid = _owner_connection_with_1001_selected(client, graph_mock, signer, as_)
    fake_repo.rows[UUID(cid)] = fake_repo.rows[UUID(cid)].model_copy(
        update={"status": AccountStatus.REAUTH_REQUIRED}
    )
    debug_route = graph_mock.post("/debug_token")
    debug_route.reset()
    debug_route.mock(return_value=httpx.Response(200, json=debug_token_payload()))
    pages_route = _rearmed_pages_route(graph_mock, pages=2)
    res = client.post(f"{BASE}/connections/{cid}/{RESYNC}", headers=as_(OWNER_ID))
    assert res.status_code == 409, res.text
    assert res.json()["detail"] == {
        "error": "reauth_required",
        "message": ReauthRequiredError().message,
        "required_action": "reconnect",
    }
    assert debug_route.called is False
    assert pages_route.called is False
    assert fake_repo.rows[UUID(cid)].status is AccountStatus.REAUTH_REQUIRED
    assert [p.page_id for p in fake_repo.rows[UUID(cid)].pages if p.is_selected] == ["1001"]


# A2-U1: a permission failure yields the single value ``reconnect`` on all four routes that
# talk to Meta (callback, resync, posts, media), and transient failures the fixed 502 body.
_PERMISSION_ERROR = {
    "error": {
        "message": "(#200) Requires pages_read_engagement",
        "type": "OAuthException",
        "code": 200,
    }
}
_FIXED_502 = {"provider": "meta", "error": "upstream_unavailable", "required_action": None}


def test_a2_u1_required_action_reconnect_is_uniform_across_meta_routes(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    as_: Headers,
) -> None:
    owner = as_(OWNER_ID)
    cid = _owner_connection_with_1001_selected(client, graph_mock, signer, as_)
    actions: dict[str, str] = {}
    fixed: dict[str, dict] = {}

    # 1) posts / 2) media — Page-token reads.
    for name, url in (("posts", _posts_url(cid)), ("media", _media_url(cid))):
        path = "/1001/posts" if name == "posts" else "/17841400000001/media"
        graph_mock.get(path).mock(return_value=httpx.Response(403, json=_PERMISSION_ERROR))
        res = client.get(url, headers=owner)
        assert res.status_code == 403, res.text
        actions[name] = res.json()["detail"]["required_action"]
        graph_mock.get(path).mock(return_value=httpx.Response(503, json={"error": {"code": 2}}))
        fixed[name] = client.get(url, headers=owner).json()["detail"]

    # 3) resync — user-token refresh.
    graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(200, json=debug_token_payload())
    )
    pages_route = _rearmed_pages_route(graph_mock, pages=2)
    pages_route.mock(return_value=httpx.Response(403, json=_PERMISSION_ERROR))
    res = client.post(f"{BASE}/connections/{cid}/{RESYNC}", headers=owner)
    assert res.status_code == 403, res.text
    actions["resync"] = res.json()["detail"]["required_action"]
    pages_route.mock(return_value=httpx.Response(503, json={"error": {"code": 2}}))
    fixed["resync"] = client.post(f"{BASE}/connections/{cid}/{RESYNC}", headers=owner).json()[
        "detail"
    ]

    # 4) callback — fresh OAuth.
    _stub_oauth(graph_mock)
    graph_mock.get("/me", params__contains={"fields": "id,name"}).mock(
        return_value=httpx.Response(400, json=_PERMISSION_ERROR)
    )
    res = _callback(client, signer)
    assert res.status_code == 403, res.text
    actions["callback"] = res.json()["detail"]["required_action"]
    graph_mock.get("/me", params__contains={"fields": "id,name"}).mock(
        return_value=httpx.Response(503, json={"error": {"code": 2}})
    )
    fixed["callback"] = _callback(client, signer).json()["detail"]

    assert set(actions) == {"posts", "media", "resync", "callback"}
    assert set(actions.values()) == {"reconnect"}
    assert all(body == _FIXED_502 for body in fixed.values()), fixed


# A3-U1: contract — the requested *root* fields of ``/{page_id}/posts`` stay inside the Post
# v26.0 allowlist and none of the fields deprecated in v3.3 is requested at the root.
_POST_V26_ROOT_ALLOWLIST = frozenset(
    {
        "id",
        "message",
        "created_time",
        "updated_time",
        "permalink_url",
        "status_type",
        "full_picture",
        "is_published",
        "is_hidden",
        "is_expired",
        "from",
        "attachments",
        "shares",
        "privacy",
        "story",
        "message_tags",
        "story_tags",
        "parent_id",
        "promotable_id",
        "icon",
        "admin_creator",
        "backdated_time",
        "scheduled_publish_time",
        "timeline_visibility",
        "is_instagram_eligible",
        "is_eligible_for_promotion",
        "is_popular",
        "is_spherical",
        "multi_share_end_card",
        "multi_share_optimized",
        "properties",
        "actions",
        "allowed_advertising_objectives",
        "application",
        "call_to_action",
        "can_reply_privately",
        "child_attachments",
        "coordinates",
        "event",
        "expanded_height",
        "expanded_width",
        "feed_targeting",
        "height",
        "instagram_eligibility",
        "place",
        "subscribed",
        "target",
        "targeting",
        "via",
        "video_buying_eligibility",
        "width",
    }
)
_POST_V33_DEPRECATED_ROOT = frozenset(
    {"caption", "description", "link", "name", "object_id", "picture", "source", "type"}
)


def _root_fields(fields: str) -> list[str]:
    """Split a Graph ``fields`` expression on top-level commas (``a,b{c,d},e`` ⇒ a, b, e)."""
    roots: list[str] = []
    depth = 0
    current = ""
    for ch in fields:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "," and depth == 0:
            roots.append(current.split("{", 1)[0])
            current = ""
            continue
        current += ch
    roots.append(current.split("{", 1)[0])
    return roots


def test_a3_u1_post_fields_contract_against_v26_reference() -> None:
    roots = _root_fields(POST_FIELDS)
    assert set(roots) <= _POST_V26_ROOT_ALLOWLIST, set(roots) - _POST_V26_ROOT_ALLOWLIST
    assert not (set(roots) & _POST_V33_DEPRECATED_ROOT)
    assert "attachments" in roots
    assert "attachments{media_type,type,url,media{image{src}}}" in POST_FIELDS
    assert {"status_type", "full_picture"} <= set(roots)


def test_a3_posts_response_flattens_attachments(
    client: TestClient,
    graph_mock: respx.MockRouter,
    signer: OAuthStateSigner,
    as_: Headers,
) -> None:
    cid = _owner_connection_with_1001_selected(client, graph_mock, signer, as_)
    payload = _posts_payload(2)
    payload["data"][0]["attachments"] = {
        "data": [
            {
                "media_type": "photo",
                "type": "photo",
                "url": "https://www.facebook.com/photo/?fbid=1",
                "media": {"image": {"src": "https://scontent.example/1.jpg", "height": 10}},
                "target": {"id": "ignored"},
            }
        ]
    }
    graph_mock.get("/1001/posts").mock(return_value=httpx.Response(200, json=payload))
    res = client.get(_posts_url(cid), headers=as_(OWNER_ID))
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert items[0]["attachments"] == [
        {
            "media_type": "photo",
            "type": "photo",
            "url": "https://www.facebook.com/photo/?fbid=1",
            "image_src": "https://scontent.example/1.jpg",
        }
    ]
    assert items[1]["attachments"] == []
    _assert_no_secrets(res.text)
