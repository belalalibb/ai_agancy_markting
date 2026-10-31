"""SPRINT-07 B4 — publishing endpoints of ``MetaGraphClient`` and the Graph → ``ErrorClass`` map.

B4-U1 request capture (form body, Bearer, ``appsecret_proof``, no token in URL or log);
B4-U2 Instagram two-step; B4-U3 six-way error classification.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from urllib.parse import parse_qsl, urlparse

import httpx
import pytest
import respx
from src.modules.publishing.domain.models import ErrorClass
from src.modules.publishing.infrastructure.meta_publisher import classify_graph_error
from src.modules.social_connect.exceptions import (
    MetaGraphError,
    MetaNetworkError,
    MetaOAuthError,
    MetaPermissionError,
    MetaRateLimitError,
    MetaTransientError,
    map_graph_error,
)
from src.modules.social_connect.meta_client import MetaGraphClient

from tests.conftest import FAKE_APP_SECRET

pytestmark = pytest.mark.contract

PAGE_TOKEN = "EAAPageTokenPublishLEAK"  # noqa: S105 - test fixture, not a credential
PAGE_ID = "1001"
IG_USER_ID = "17841400000001"


def _form(request: httpx.Request) -> dict[str, str]:
    assert request.headers["Content-Type"].startswith("application/x-www-form-urlencoded")
    return dict(parse_qsl(request.content.decode("utf-8")))


def _expected_proof(token: str) -> str:
    return hmac.new(FAKE_APP_SECRET.encode(), token.encode(), hashlib.sha256).hexdigest()


def _assert_auth(request: httpx.Request) -> None:
    """Bearer header, correct proof, token never in the URL (B4-U1)."""
    assert request.headers["Authorization"] == f"Bearer {PAGE_TOKEN}"
    query = dict(parse_qsl(urlparse(str(request.url)).query))
    assert query["appsecret_proof"] == _expected_proof(PAGE_TOKEN)
    assert "access_token" not in query
    assert PAGE_TOKEN not in str(request.url)


# ------------------------------------------------------------------ B4-U1 ---
async def test_b4_u1_publish_page_post_request_shape_and_permalink(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter, caplog: pytest.LogCaptureFixture
) -> None:
    feed = graph_mock.post(f"/{PAGE_ID}/feed").mock(
        return_value=httpx.Response(200, json={"id": f"{PAGE_ID}_555"})
    )
    permalink = graph_mock.get(f"/{PAGE_ID}_555").mock(
        return_value=httpx.Response(
            200, json={"permalink_url": "https://www.facebook.com/1001/posts/555"}
        )
    )
    with caplog.at_level(logging.DEBUG):
        result = await meta_client.publish_page_post(
            PAGE_TOKEN, PAGE_ID, "Hello world", link="https://example.com/offer"
        )

    assert result.post_id == f"{PAGE_ID}_555"
    assert result.permalink == "https://www.facebook.com/1001/posts/555"
    assert feed.call_count == 1 and permalink.call_count == 1
    request = feed.calls[0].request
    assert _form(request) == {"message": "Hello world", "link": "https://example.com/offer"}
    _assert_auth(request)
    _assert_auth(permalink.calls[0].request)
    assert dict(parse_qsl(urlparse(str(permalink.calls[0].request.url)).query))["fields"] == (
        "permalink_url"
    )
    assert PAGE_TOKEN not in caplog.text


async def test_b4_u1_permalink_lookup_failure_does_not_fail_the_publish(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    graph_mock.post(f"/{PAGE_ID}/feed").mock(return_value=httpx.Response(200, json={"id": "p1"}))
    graph_mock.get("/p1").mock(
        return_value=httpx.Response(
            400, json={"error": {"message": "x", "code": 100, "type": "GraphMethodException"}}
        )
    )
    result = await meta_client.publish_page_post(PAGE_TOKEN, PAGE_ID, "Hello")
    assert result.post_id == "p1"
    assert result.permalink is None


async def test_b4_u1_publish_page_post_without_link_omits_field(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    feed = graph_mock.post(f"/{PAGE_ID}/feed").mock(
        return_value=httpx.Response(200, json={"id": "p2"})
    )
    graph_mock.get("/p2").mock(return_value=httpx.Response(200, json={"permalink_url": ""}))
    result = await meta_client.publish_page_post(PAGE_TOKEN, PAGE_ID, "Only text")
    assert _form(feed.calls[0].request) == {"message": "Only text"}
    assert result.permalink is None  # empty string ⇒ None


async def test_b4_u1_malformed_publish_response_raises(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    graph_mock.post(f"/{PAGE_ID}/feed").mock(return_value=httpx.Response(200, json={"ok": 1}))
    with pytest.raises(MetaGraphError):
        await meta_client.publish_page_post(PAGE_TOKEN, PAGE_ID, "Hello")


# ------------------------------------------------------------------ B4-U2 ---
async def test_b4_u2_instagram_two_step(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    media = graph_mock.post(f"/{IG_USER_ID}/media").mock(
        return_value=httpx.Response(200, json={"id": "creation-1"})
    )
    publish = graph_mock.post(f"/{IG_USER_ID}/media_publish").mock(
        return_value=httpx.Response(200, json={"id": "media-9"})
    )
    link = graph_mock.get("/media-9").mock(
        return_value=httpx.Response(200, json={"permalink": "https://www.instagram.com/p/abc/"})
    )

    creation_id = await meta_client.create_instagram_container(
        PAGE_TOKEN, IG_USER_ID, "https://cdn.example.com/a.jpg", "caption"
    )
    assert creation_id == "creation-1"
    assert _form(media.calls[0].request) == {
        "image_url": "https://cdn.example.com/a.jpg",
        "caption": "caption",
    }
    _assert_auth(media.calls[0].request)

    result = await meta_client.publish_instagram_container(PAGE_TOKEN, IG_USER_ID, creation_id)
    assert result.post_id == "media-9"
    assert result.permalink == "https://www.instagram.com/p/abc/"
    assert _form(publish.calls[0].request) == {"creation_id": "creation-1"}
    _assert_auth(publish.calls[0].request)
    assert dict(parse_qsl(urlparse(str(link.calls[0].request.url)).query))["fields"] == (
        "permalink"
    )


async def test_b4_u2_media_publish_error_propagates_as_graph_error(
    meta_client: MetaGraphClient, graph_mock: respx.MockRouter
) -> None:
    graph_mock.post(f"/{IG_USER_ID}/media_publish").mock(
        return_value=httpx.Response(
            400,
            json={"error": {"message": "Media not ready", "code": 9007, "type": "OAuthException"}},
        )
    )
    with pytest.raises(MetaOAuthError) as info:
        await meta_client.publish_instagram_container(PAGE_TOKEN, IG_USER_ID, "creation-1")
    assert info.value.code == 9007


# ------------------------------------------------------------------ B4-U3 ---
def _graph(code: int | None, *, http_status: int = 400, error_type: str = "OAuthException"):  # noqa: ANN202
    payload = {"error": {"message": "LEAK-MESSAGE", "code": code, "type": error_type}}
    return map_graph_error(payload, http_status)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        pytest.param(_graph(190), ErrorClass.TOKEN_INVALID, id="190-token"),
        pytest.param(_graph(102), ErrorClass.TOKEN_INVALID, id="102-session"),
        pytest.param(_graph(10), ErrorClass.PERMISSION_DENIED, id="10-permission"),
        pytest.param(_graph(200), ErrorClass.PERMISSION_DENIED, id="200-permission"),
        pytest.param(_graph(299), ErrorClass.PERMISSION_DENIED, id="299-permission"),
        pytest.param(_graph(4), ErrorClass.RATE_LIMITED, id="4-app-rate"),
        pytest.param(_graph(17), ErrorClass.RATE_LIMITED, id="17-user-rate"),
        pytest.param(_graph(32), ErrorClass.RATE_LIMITED, id="32-page-rate"),
        pytest.param(_graph(613), ErrorClass.RATE_LIMITED, id="613-custom-rate"),
        pytest.param(_graph(1), ErrorClass.TRANSIENT, id="1-unknown-api"),
        pytest.param(_graph(2), ErrorClass.TRANSIENT, id="2-service"),
        pytest.param(
            _graph(None, http_status=503, error_type="Other"), ErrorClass.TRANSIENT, id="5xx"
        ),
        pytest.param(MetaNetworkError("timeout"), ErrorClass.TRANSIENT, id="network"),
        pytest.param(
            _graph(100, error_type="GraphMethodException"),
            ErrorClass.INVALID_REQUEST,
            id="100-invalid",
        ),
        pytest.param(_graph(9007, error_type="Other"), ErrorClass.UNKNOWN, id="other-code"),
        pytest.param(MetaGraphError("no code"), ErrorClass.UNKNOWN, id="no-code"),
    ],
)
def test_b4_u3_graph_error_map(exc: MetaGraphError, expected: ErrorClass) -> None:
    assert classify_graph_error(exc) is expected


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (MetaRateLimitError("x"), ErrorClass.RATE_LIMITED),
        (MetaPermissionError("x"), ErrorClass.PERMISSION_DENIED),
        (MetaOAuthError("x"), ErrorClass.TOKEN_INVALID),
        (MetaTransientError("x"), ErrorClass.TRANSIENT),
    ],
)
def test_b4_u3_type_fallback_when_code_missing(exc: MetaGraphError, expected: ErrorClass) -> None:
    assert classify_graph_error(exc) is expected


def test_b4_u3_error_class_values_are_labels_not_messages() -> None:
    exc = _graph(190)
    assert "LEAK-MESSAGE" not in classify_graph_error(exc).value
