"""Unit tests — Graph API error payload → typed exception mapping."""

from __future__ import annotations

import pytest
from src.modules.social_connect.exceptions import (
    MetaGraphError,
    MetaOAuthError,
    MetaPermissionError,
    MetaRateLimitError,
    MetaTokenExpiredError,
    MetaTransientError,
    map_graph_error,
)

from tests.conftest import oauth_error_payload

pytestmark = pytest.mark.unit


def test_expired_token_maps_to_token_expired() -> None:
    exc = map_graph_error(oauth_error_payload(code=190, subcode=463), 400)
    assert isinstance(exc, MetaTokenExpiredError)
    assert isinstance(exc, MetaOAuthError)
    assert exc.code == 190
    assert exc.subcode == 463
    assert exc.fbtrace_id == "AbCdEf"
    assert "expired" in str(exc).lower()
    assert "code=190" in str(exc)


def test_code_190_without_subcode_is_treated_as_expired() -> None:
    exc = map_graph_error(oauth_error_payload(code=190, subcode=None), 400)
    assert isinstance(exc, MetaTokenExpiredError)


def test_generic_oauth_exception_without_190() -> None:
    payload = oauth_error_payload(
        message="Invalid verification code format.", code=100, subcode=None
    )
    exc = map_graph_error(payload, 400)
    assert type(exc) is MetaOAuthError


def test_http_401_without_body_is_oauth_error() -> None:
    exc = map_graph_error(None, 401)
    assert isinstance(exc, MetaOAuthError)
    assert exc.http_status == 401


@pytest.mark.parametrize("code", [4, 17, 32, 613, 80001])
def test_rate_limit_codes(code: int) -> None:
    payload = {"error": {"message": "Application request limit reached", "code": code}}
    exc = map_graph_error(payload, 400, retry_after_seconds=30)
    assert isinstance(exc, MetaRateLimitError)
    assert exc.retry_after_seconds == 30


def test_http_429_is_rate_limit_even_without_code() -> None:
    assert isinstance(map_graph_error({}, 429), MetaRateLimitError)


@pytest.mark.parametrize("code", [10, 200, 230, 299])
def test_permission_codes(code: int) -> None:
    payload = {
        "error": {"message": "Permissions error", "code": code, "type": "GraphMethodException"}
    }
    assert isinstance(map_graph_error(payload, 403), MetaPermissionError)


@pytest.mark.parametrize(("code", "status"), [(1, 500), (2, 503), (None, 502)])
def test_transient_errors(code: int | None, status: int) -> None:
    payload = {"error": {"message": "Please retry", "code": code}} if code else {}
    assert isinstance(map_graph_error(payload, status), MetaTransientError)


def test_unknown_error_falls_back_to_base() -> None:
    payload = {
        "error": {
            "message": "Unsupported get request",
            "code": 100,
            "type": "GraphMethodException",
        }
    }
    exc = map_graph_error(payload, 400)
    assert type(exc) is MetaGraphError
    assert exc.message == "Unsupported get request"
    assert exc.raw == payload


def test_non_numeric_codes_are_tolerated() -> None:
    payload = {"error": {"message": "weird", "code": "abc", "error_subcode": "x"}}
    exc = map_graph_error(payload, 400)
    assert exc.code is None
    assert exc.subcode is None
