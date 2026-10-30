"""Contract tests — MetaGraphClient against a mocked Meta Graph API (respx).

No real network traffic: every endpoint is stubbed and unmatched requests fail.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from urllib.parse import parse_qs, parse_qsl, urlparse

import httpx
import pytest
import respx
from src.core.config import Settings
from src.modules.social_connect.exceptions import (
    MetaGraphError,
    MetaNetworkError,
    MetaOAuthError,
    MetaPermissionError,
    MetaRateLimitError,
    MetaTokenExpiredError,
    MetaTransientError,
)
from src.modules.social_connect.meta_client import (
    MAX_PAGINATION_REQUESTS,
    PAGE_FIELDS,
    MetaGraphClient,
)

from tests.conftest import (
    FAKE_APP_ID,
    FAKE_APP_SECRET,
    GRAPH_BASE,
    GRAPH_VERSION,
    oauth_error_payload,
    pages_payload,
    token_payload,
)

pytestmark = pytest.mark.contract


def _form(request: httpx.Request) -> dict[str, str]:
    """Decode an ``application/x-www-form-urlencoded`` request body."""
    assert request.headers["Content-Type"].startswith("application/x-www-form-urlencoded")
    return dict(parse_qsl(request.content.decode("utf-8")))


# ------------------------------------------------------------ authorize URL
def test_build_authorize_url_contains_required_params(meta_client: MetaGraphClient) -> None:
    url, state = meta_client.build_authorize_url("https://app.example.com/cb", state="csrf123")
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert parsed.netloc == "www.facebook.com"
    assert parsed.path == f"/{GRAPH_VERSION}/dialog/oauth"
    assert qs["client_id"] == [FAKE_APP_ID]
    assert qs["redirect_uri"] == ["https://app.example.com/cb"]
    assert qs["state"] == ["csrf123"]
    assert qs["response_type"] == ["code"]
    assert "pages_show_list" in qs["scope"][0]
    assert state == "csrf123"
    assert FAKE_APP_SECRET not in url


def test_build_authorize_url_generates_random_state(meta_client: MetaGraphClient) -> None:
    _, s1 = meta_client.build_authorize_url("https://a.b/c")
    _, s2 = meta_client.build_authorize_url("https://a.b/c")
    assert s1 != s2
    assert len(s1) >= 32


def test_build_authorize_url_custom_scopes(meta_client: MetaGraphClient) -> None:
    url, _ = meta_client.build_authorize_url("https://a.b/c", scopes=["email"])
    assert parse_qs(urlparse(url).query)["scope"] == ["email"]


def test_appsecret_proof_is_hmac_sha256(meta_client: MetaGraphClient) -> None:
    expected = hmac.new(FAKE_APP_SECRET.encode(), b"TOKEN", hashlib.sha256).hexdigest()
    assert meta_client.appsecret_proof("TOKEN") == expected


# ------------------------------------------------------------- OAuth happy
async def test_exchange_code_for_long_lived_token(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    short_route = graph_mock.post(
        "/oauth/access_token", data__contains={"code": "AUTH_CODE"}
    ).mock(return_value=httpx.Response(200, json=token_payload("SHORT", expires_in=5000)))
    long_route = graph_mock.post(
        "/oauth/access_token", data__contains={"grant_type": "fb_exchange_token"}
    ).mock(return_value=httpx.Response(200, json=token_payload("LONG", expires_in=5183944)))

    token = await meta_client.exchange_code_for_token("AUTH_CODE", "https://app.example.com/cb")

    assert short_route.called and long_route.called
    assert token.short_lived_token is not None
    assert token.short_lived_token.get_secret_value() == "SHORT"
    assert token.long_lived_token.get_secret_value() == "LONG"
    assert token.expires_in == 5183944
    assert token.expires_at is not None

    # H2: credentials travel in the form body — the request line carries no query at all.
    short_req = short_route.calls[0].request
    assert short_req.url.query == b""
    short_form = _form(short_req)
    assert short_form["client_id"] == FAKE_APP_ID
    assert short_form["client_secret"] == FAKE_APP_SECRET
    assert short_form["redirect_uri"] == "https://app.example.com/cb"
    long_req = long_route.calls[0].request
    assert long_req.url.query == b""
    assert _form(long_req)["fb_exchange_token"] == "SHORT"


async def test_exchange_long_lived_defaults_when_fields_missing(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    graph_mock.post("/oauth/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "LONG"})
    )
    token = await meta_client.exchange_for_long_lived_token("SHORT")
    assert token.expires_in == 0
    assert token.expires_at is None
    assert token.token_type == "bearer"


async def test_malformed_token_response_raises(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    graph_mock.post("/oauth/access_token").mock(return_value=httpx.Response(200, json={"foo": 1}))
    with pytest.raises(MetaGraphError, match="missing 'access_token'"):
        await meta_client.exchange_code_for_token("X", "https://a.b/c")


async def test_invalid_code_raises_oauth_error(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    graph_mock.post("/oauth/access_token").mock(
        return_value=httpx.Response(
            400,
            json=oauth_error_payload(
                message="Invalid verification code format.", code=100, subcode=None
            ),
        )
    )
    with pytest.raises(MetaOAuthError) as info:
        await meta_client.exchange_code_for_token("BAD", "https://a.b/c")
    assert not isinstance(info.value, MetaTokenExpiredError)
    assert info.value.code == 100


# ------------------------------------------------------------------ Pages
async def test_get_user_pages_returns_pages_and_instagram(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    route = graph_mock.get("/me/accounts").mock(
        return_value=httpx.Response(200, json=pages_payload())
    )
    pages = await meta_client.get_user_pages("USER_TOKEN")

    assert len(pages) == 2
    assert pages[0].page_id == "1001"
    assert pages[0].instagram_business_account_id == "17841400000001"
    assert pages[1].instagram_business_account_id is None
    assert pages[0].page_access_token is not None

    req = route.calls[0].request
    qs = dict(req.url.params)
    assert "access_token" not in qs  # H2
    assert req.headers["Authorization"] == "Bearer USER_TOKEN"
    assert qs["fields"] == PAGE_FIELDS
    assert qs["appsecret_proof"] == meta_client.appsecret_proof("USER_TOKEN")


async def test_get_user_pages_follows_pagination(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    page1 = pages_payload()
    page1["paging"]["next"] = f"{GRAPH_BASE}/me/accounts?after=b&access_token=USER_TOKEN"
    page2 = {"data": [{"id": "1003", "name": "Third", "tasks": []}], "paging": {}}

    second = graph_mock.get("/me/accounts", params__contains={"after": "b"}).mock(
        return_value=httpx.Response(200, json=page2)
    )
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, json=page1))

    pages = await meta_client.get_user_pages("USER_TOKEN")
    assert [p.page_id for p in pages] == ["1001", "1002", "1003"]
    # H2: the token Meta echoed in ``paging.next`` is stripped and re-sent as Bearer.
    follow = second.calls[0].request
    assert "access_token" not in dict(follow.url.params)
    assert follow.headers["Authorization"] == "Bearer USER_TOKEN"
    assert dict(follow.url.params)["appsecret_proof"] == meta_client.appsecret_proof("USER_TOKEN")


async def test_get_user_pages_empty(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, json={"data": []}))
    assert await meta_client.get_user_pages("USER_TOKEN") == []


# ------------------------------------------------------- OAuthException 190
async def test_expired_token_raises_token_expired(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    graph_mock.get("/me/accounts").mock(
        return_value=httpx.Response(400, json=oauth_error_payload(code=190, subcode=463))
    )
    with pytest.raises(MetaTokenExpiredError) as info:
        await meta_client.get_user_pages("EXPIRED_TOKEN")
    err = info.value
    assert err.error_type == "OAuthException"
    assert err.code == 190
    assert err.subcode == 463
    assert err.http_status == 400
    assert "Session has expired" in err.message


async def test_oauth_errors_are_not_retried(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    route = graph_mock.get("/me/accounts").mock(
        return_value=httpx.Response(401, json=oauth_error_payload())
    )
    with pytest.raises(MetaOAuthError):
        await meta_client.get_user_pages("T")
    assert route.call_count == 1


# --------------------------------------------------------------- Permissions
async def test_permission_error_with_oauth_type_maps_to_403(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    """SPRINT-02 A4: permission codes (10 / 200-299) win over ``type == OAuthException``.

    (Renamed from ``test_missing_permission_with_oauth_type_requires_reauth`` — the
    planner's SPRINT-01 review §5 reversed the 401/403 precedence.)
    """
    payload = {
        "error": {
            "message": "(#200) Requires pages_show_list permission",
            "type": "OAuthException",
            "code": 200,
        }
    }
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(403, json=payload))
    with pytest.raises(MetaPermissionError) as info:
        await meta_client.get_user_pages("T")
    assert not isinstance(info.value, MetaOAuthError | MetaTokenExpiredError)
    assert info.value.code == 200


async def test_code_190_with_oauth_type_still_maps_to_401(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    graph_mock.get("/me/accounts").mock(
        return_value=httpx.Response(400, json=oauth_error_payload(code=190, subcode=None))
    )
    with pytest.raises(MetaTokenExpiredError):
        await meta_client.get_user_pages("T")


async def test_permission_code_10_without_oauth_type(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    payload = {
        "error": {
            "message": "(#10) Permission denied",
            "code": 10,
            "type": "GraphMethodException",
        }
    }
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(403, json=payload))
    with pytest.raises(MetaPermissionError):
        await meta_client.get_user_pages("T")


# --------------------------------------------------------------- Rate limit
# SPRINT-02 A3 (C4): rate limits are *never* retried. The former
# ``test_rate_limit_retries_then_succeeds`` / ``test_rate_limit_exhausts_retries`` are
# superseded by the two tests below (Meta extends the block if you keep calling).
async def test_rate_limit_raises_immediately_without_retry(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    throttled = httpx.Response(
        400,
        json={"error": {"message": "(#4) Application request limit reached", "code": 4}},
        headers={"Retry-After": "1"},
    )
    route = graph_mock.get("/me/accounts").mock(
        side_effect=[throttled, httpx.Response(200, json=pages_payload())]
    )
    with pytest.raises(MetaRateLimitError) as info:
        await meta_client.get_user_pages("T")
    assert route.call_count == 1
    assert info.value.retry_after_seconds == 1.0


async def test_http_429_raises_immediately_and_keeps_status(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    route = graph_mock.get("/me/accounts").mock(
        return_value=httpx.Response(429, json={"error": {"message": "slow down", "code": 32}})
    )
    with pytest.raises(MetaRateLimitError) as info:
        await meta_client.get_user_pages("T")
    assert info.value.http_status == 429
    assert info.value.retry_after_seconds is None
    assert route.call_count == 1


async def test_transient_5xx_still_retries(
    settings: Settings, graph_mock: respx.MockRouter
) -> None:
    """A3 must not break the Transient path: 503 → backoff → 503 → backoff → 200."""
    sleeps: list[float] = []

    async def _record(delay: float) -> None:
        sleeps.append(delay)

    async with httpx.AsyncClient() as http:
        client = MetaGraphClient(settings=settings, http_client=http, sleep=_record)
        route = graph_mock.get("/me/accounts").mock(
            side_effect=[
                httpx.Response(503, json={}),
                httpx.Response(503, json={}),
                httpx.Response(200, json={"data": []}),
            ]
        )
        pages = await client.get_user_pages("T")
    assert pages == []
    assert route.call_count == 3
    assert sleeps == [0.5, 1.0]  # exponential: 0.5 * 2**attempt


async def test_backoff_honours_retry_after(
    settings: Settings, graph_mock: respx.MockRouter
) -> None:
    """``Retry-After`` is no longer used for sleeping (no retry on 429) but it must
    still be parsed and surfaced on the exception for the API layer's header."""
    sleeps: list[float] = []

    async def _record(delay: float) -> None:
        sleeps.append(delay)

    async with httpx.AsyncClient() as http:
        client = MetaGraphClient(settings=settings, http_client=http, sleep=_record)
        graph_mock.get("/me/accounts").mock(
            return_value=httpx.Response(429, json={}, headers={"Retry-After": "7"})
        )
        with pytest.raises(MetaRateLimitError) as info:
            await client.get_user_pages("T")
    assert info.value.retry_after_seconds == 7.0
    assert sleeps == []


async def test_invalid_retry_after_header_falls_back_to_exponential(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    """An unparseable ``Retry-After`` yields ``None`` (callers fall back to their own policy)."""
    graph_mock.get("/me/accounts").mock(
        return_value=httpx.Response(429, json={}, headers={"Retry-After": "Wed, 21 Oct 2026"})
    )
    with pytest.raises(MetaRateLimitError) as info:
        await meta_client.get_user_pages("T")
    assert info.value.retry_after_seconds is None


# ---------------------------------------------------------- Transient / net
async def test_server_error_retries_then_raises(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    route = graph_mock.get("/me/accounts").mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(MetaTransientError):
        await meta_client.get_user_pages("T")
    assert route.call_count == 3


async def test_timeout_is_retried_then_raised_as_network_error(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    route = graph_mock.get("/me/accounts").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(MetaNetworkError, match="timeout"):
        await meta_client.get_user_pages("T")
    assert route.call_count == 3


async def test_connection_error_is_not_retried(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    route = graph_mock.get("/me/accounts").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(MetaNetworkError, match="network error"):
        await meta_client.get_user_pages("T")
    assert route.call_count == 1


async def test_non_json_success_body_is_tolerated(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, text="<html/>"))
    assert await meta_client.get_user_pages("T") == []


async def test_list_json_body_is_wrapped(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    graph_mock.get("/me/accounts").mock(
        return_value=httpx.Response(200, json=[{"id": "7", "name": "L"}])
    )
    pages = await meta_client.get_user_pages("T")
    assert [p.page_id for p in pages] == ["7"]


async def test_context_manager_closes_owned_client(settings: Settings) -> None:
    async with MetaGraphClient(settings=settings) as client:
        assert client.base_url == GRAPH_BASE
        assert client.is_closed is False
    assert client.is_closed is True


async def test_injected_client_is_not_closed(settings: Settings) -> None:
    async with httpx.AsyncClient() as http:
        async with MetaGraphClient(settings=settings, http_client=http):
            pass
        assert http.is_closed is False


# ------------------------------------------------------------ debug_token
async def test_debug_token_parses_payload(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    route = graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "app_id": FAKE_APP_ID,
                    "is_valid": True,
                    "user_id": 42,
                    "scopes": ["pages_show_list"],
                    "expires_at": 1_800_000_000,
                }
            },
        )
    )
    info = await meta_client.debug_token("TOKEN")
    assert info.is_valid is True
    assert info.user_id == "42"
    assert info.scopes == ["pages_show_list"]
    assert info.expires_at == 1_800_000_000
    req = route.calls[0].request
    assert req.url.query == b""  # H2: nothing secret in the request line
    assert req.headers["Authorization"] == f"Bearer {FAKE_APP_ID}|{FAKE_APP_SECRET}"
    assert _form(req) == {"input_token": "TOKEN", "method": "GET"}


async def test_a0_u1_debug_token_is_post_with_method_get_override_in_body(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    """SPRINT-05 A0-U1 — ``POST /debug_token`` carries ``method=GET`` + ``input_token`` in the
    form body only; the request line has neither ``input_token`` nor ``access_token``."""
    route = graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(200, json={"data": {"app_id": FAKE_APP_ID, "is_valid": True}})
    )
    await meta_client.debug_token("INSPECTED_TOKEN")
    req = route.calls[0].request
    assert req.method == "POST"
    body = _form(req)
    assert body["method"] == "GET"
    assert body["input_token"] == "INSPECTED_TOKEN"
    url = str(req.url)
    assert "input_token" not in url
    assert "access_token" not in url
    assert "method" not in url
    assert "INSPECTED_TOKEN" not in url
    assert req.headers["authorization"].startswith("Bearer ")
    assert req.headers["authorization"] == f"Bearer {FAKE_APP_ID}|{FAKE_APP_SECRET}"


async def test_debug_token_handles_sparse_payload(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    graph_mock.post("/debug_token").mock(return_value=httpx.Response(200, json={"data": {}}))
    info = await meta_client.debug_token("TOKEN")
    assert info.is_valid is False
    assert info.user_id is None
    assert info.expires_at is None


# -------------------------------------------------------- SPRINT-02.1 (H2, H13)
async def test_get_me_returns_identity_and_sends_appsecret_proof(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    """SPRINT-02.1 H2 — ``GET /me?fields=id,name`` with Bearer token + ``appsecret_proof``."""
    route = graph_mock.get("/me").mock(
        return_value=httpx.Response(200, json={"id": "10001", "name": "Nile Owner"})
    )
    me = await meta_client.get_me("USER_TOKEN")
    assert me.id == "10001"
    assert me.name == "Nile Owner"
    req = route.calls[0].request
    qs = dict(req.url.params)
    assert qs["fields"] == "id,name"
    assert "access_token" not in qs  # H2
    assert req.headers["Authorization"] == "Bearer USER_TOKEN"
    assert qs["appsecret_proof"] == meta_client.appsecret_proof("USER_TOKEN")
    assert req.url.path.endswith(f"/{GRAPH_VERSION}/me")


async def test_get_me_without_name_and_numeric_id(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    graph_mock.get("/me").mock(return_value=httpx.Response(200, json={"id": "7", "name": ""}))
    me = await meta_client.get_me("T")
    assert me.id == "7"
    assert me.name is None


async def test_get_me_missing_id_is_malformed(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    graph_mock.get("/me").mock(return_value=httpx.Response(200, json={"name": "x"}))
    with pytest.raises(MetaGraphError, match="missing 'id'"):
        await meta_client.get_me("T")


async def test_get_me_expired_token_raises_token_expired(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    graph_mock.get("/me").mock(return_value=httpx.Response(401, json=oauth_error_payload()))
    with pytest.raises(MetaTokenExpiredError):
        await meta_client.get_me("T")


async def test_debug_token_reads_error_code(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "app_id": FAKE_APP_ID,
                    "is_valid": False,
                    "error": {"code": 190, "message": "Invalid OAuth access token."},
                }
            },
        )
    )
    info = await meta_client.debug_token("TOKEN")
    assert info.is_valid is False
    assert info.error_code == 190


async def test_debug_token_error_code_tolerates_garbage(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(
            200, json={"data": {"is_valid": False, "error": {"code": "not-a-number"}}}
        )
    )
    assert (await meta_client.debug_token("TOKEN")).error_code is None
    graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(200, json={"data": {"is_valid": False, "error": "weird"}})
    )
    assert (await meta_client.debug_token("TOKEN")).error_code is None


async def test_pagination_cap_raises_after_max(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    """H13 — a ``paging.next`` that never ends is cut off at ``MAX_PAGINATION_REQUESTS``."""
    endless = {
        "data": [{"id": "1", "name": "Loop", "tasks": []}],
        "paging": {"next": f"{GRAPH_BASE}/me/accounts?after=zzz&access_token=T"},
    }
    route = graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, json=endless))
    with pytest.raises(MetaGraphError, match="MAX_PAGINATION_REQUESTS") as exc_info:
        await meta_client.get_user_pages("T")
    assert route.call_count == MAX_PAGINATION_REQUESTS
    assert exc_info.value.http_status == 200
    assert MAX_PAGINATION_REQUESTS == 50


async def test_pagination_exactly_at_cap_succeeds(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    """Exactly ``MAX_PAGINATION_REQUESTS`` hops (last one without ``next``) is allowed."""
    counter = {"n": 0}

    def _respond(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        body: dict = {"data": [{"id": str(counter["n"]), "name": "P", "tasks": []}]}
        if counter["n"] < MAX_PAGINATION_REQUESTS:
            body["paging"] = {"next": f"{GRAPH_BASE}/me/accounts?after={counter['n']}"}
        return httpx.Response(200, json=body)

    graph_mock.get("/me/accounts").mock(side_effect=_respond)
    pages = await meta_client.get_user_pages("T")
    assert len(pages) == MAX_PAGINATION_REQUESTS
    assert counter["n"] == MAX_PAGINATION_REQUESTS


async def test_repeated_5xx_stops_after_max_retries_plus_one(
    settings: Settings, graph_mock: respx.MockRouter
) -> None:
    """F8 — ``_request_absolute`` is bounded: ``max_retries + 1`` attempts, then raise."""
    sleeps: list[float] = []

    async def _record(delay: float) -> None:
        sleeps.append(delay)

    route = graph_mock.get("/me").mock(return_value=httpx.Response(503, text="down"))
    for max_retries in (0, 1, 4):
        before = route.call_count
        cfg = settings.model_copy(update={"meta_http_max_retries": max_retries})
        async with httpx.AsyncClient() as http:
            client = MetaGraphClient(settings=cfg, http_client=http, sleep=_record)
            with pytest.raises(MetaTransientError):
                await client.get_me("T")
        assert route.call_count - before == max_retries + 1, max_retries
        assert len(sleeps) == max_retries
        sleeps.clear()


# ------------------------------------------------------ SPRINT-04.1 H2 / H4
_URL_SECRET_KEYS = ("access_token", "client_secret", "input_token", "fb_exchange_token")


async def test_h2_u1_no_graph_request_carries_a_secret_in_the_url(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    """H2-U1 — every ``MetaGraphClient`` path: no secret key in ``request.url.query``."""
    graph_mock.post(
        "/oauth/access_token", data__contains={"grant_type": "fb_exchange_token"}
    ).mock(return_value=httpx.Response(200, json=token_payload("LONG", expires_in=100)))
    graph_mock.post("/oauth/access_token").mock(
        return_value=httpx.Response(200, json=token_payload("SHORT", expires_in=10))
    )
    graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(200, json={"data": {"is_valid": True}})
    )
    graph_mock.get("/me").mock(return_value=httpx.Response(200, json={"id": "1"}))
    page1 = pages_payload()
    page1["paging"]["next"] = f"{GRAPH_BASE}/me/accounts?after=b&access_token=USER_TOKEN"
    graph_mock.get("/me/accounts", params__contains={"after": "b"}).mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, json=page1))

    await meta_client.exchange_code_for_token("AUTH_CODE", "https://app.example.com/cb")
    await meta_client.exchange_for_long_lived_token("SHORT")
    await meta_client.debug_token("LONG")
    await meta_client.get_me("LONG")
    await meta_client.get_user_pages("USER_TOKEN")

    assert len(graph_mock.calls) == 7
    for call in graph_mock.calls:
        query = call.request.url.query.decode()
        keys = {k for k, _ in parse_qsl(query, keep_blank_values=True)}
        assert not keys.intersection(_URL_SECRET_KEYS), (call.request.method, query)
        for raw in ("USER_TOKEN", "SHORT", "LONG", FAKE_APP_SECRET, "AUTH_CODE"):
            assert raw not in query, (call.request.method, query)
        assert "code=" not in query


@pytest.mark.parametrize(
    "next_url",
    [
        "http://evil.example/v26.0/me/accounts?after=b",
        "https://evil.example/v26.0/me/accounts?after=b",
        "https://graph.facebook.com.evil.example/v26.0/me/accounts?after=b",
        "//evil.example/me/accounts",
        "ftp://graph.facebook.com/me/accounts",
    ],
)
async def test_h4_u1_untrusted_pagination_url_is_never_requested(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter, next_url: str
) -> None:
    """H4-U1 — ``paging.next`` off-host / non-https ⇒ ``MetaGraphError``, no request."""
    page1 = pages_payload()
    page1["paging"]["next"] = next_url
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, json=page1))
    with respx.mock(assert_all_called=False) as other_hosts:
        evil = other_hosts.route(host__regex=r".*evil.*").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        with pytest.raises(MetaGraphError, match="untrusted pagination url"):
            await meta_client.get_user_pages("USER_TOKEN")
        assert evil.call_count == 0
    assert len(graph_mock.calls) == 1


async def test_h2_network_error_messages_do_not_embed_the_request(
    meta_client: MetaGraphClient,
    graph_mock: respx.MockRouter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """H2 (3) — ``MetaNetworkError`` never carries ``str(exc)``; the log names the type."""
    graph_mock.get("/me/accounts").mock(
        side_effect=httpx.ConnectError("boom https://x/?access_token=LEAK")
    )
    with caplog.at_level(logging.WARNING, logger="src.modules.social_connect.meta_client"):
        with pytest.raises(MetaNetworkError) as info:
            await meta_client.get_user_pages("LEAK")
    assert str(info.value) == "Meta Graph API network error"
    assert "LEAK" not in str(info.value)
    assert "LEAK" not in caplog.text
    assert "ConnectError" in caplog.text

    graph_mock.get("/me/accounts").mock(side_effect=httpx.ReadTimeout("slow ?access_token=LEAK"))
    with pytest.raises(MetaNetworkError) as info:
        await meta_client.get_user_pages("LEAK")
    assert str(info.value) == "Meta Graph API timeout"
