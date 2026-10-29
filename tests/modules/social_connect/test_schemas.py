"""Unit tests — Pydantic contracts for the Meta connector."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from src.modules.social_connect.schemas import (
    MetaAuthRequest,
    MetaConnectionResult,
    MetaPageInfo,
    MetaTokenResponse,
)

from tests.conftest import pages_payload

pytestmark = pytest.mark.unit


# --------------------------------------------------------------- AuthRequest
def test_auth_request_valid() -> None:
    req = MetaAuthRequest(code="  AQBx…  ", redirect_uri="https://app.example.com/cb", state="s1")
    assert req.code == "AQBx…"
    assert str(req.redirect_uri) == "https://app.example.com/cb"
    assert req.state == "s1"


@pytest.mark.parametrize(
    "payload",
    [
        {"code": "", "redirect_uri": "https://a.b/c"},
        {"code": "x", "redirect_uri": "not-a-url"},
        {"code": "x", "redirect_uri": "https://a.b/c", "unexpected": 1},
        {"redirect_uri": "https://a.b/c"},
    ],
)
def test_auth_request_invalid(payload: dict) -> None:
    with pytest.raises(ValidationError):
        MetaAuthRequest(**payload)


# ------------------------------------------------------------- TokenResponse
def test_token_response_expiry_computed() -> None:
    issued = datetime(2026, 1, 1, tzinfo=UTC)
    tok = MetaTokenResponse(long_lived_token="LL", expires_in=3600, issued_at=issued)
    assert tok.expires_at == issued + timedelta(hours=1)
    assert tok.token_type == "bearer"
    assert tok.short_lived_token is None


def test_token_response_zero_expiry_means_never() -> None:
    tok = MetaTokenResponse(long_lived_token="LL", expires_in=0)
    assert tok.expires_at is None


def test_token_response_masks_secrets_in_json_and_repr() -> None:
    tok = MetaTokenResponse(
        short_lived_token="SL_SECRET", long_lived_token="LL_SECRET", expires_in=10
    )
    dumped = tok.model_dump_json()
    assert "LL_SECRET" not in dumped
    assert "SL_SECRET" not in dumped
    assert json.loads(dumped)["long_lived_token"] == "***"
    assert "LL_SECRET" not in repr(tok)
    assert tok.long_lived_token.get_secret_value() == "LL_SECRET"


def test_token_response_rejects_negative_expiry() -> None:
    with pytest.raises(ValidationError):
        MetaTokenResponse(long_lived_token="LL", expires_in=-1)


# ------------------------------------------------------------------ PageInfo
def test_page_info_from_graph_flattens_instagram_account() -> None:
    node = pages_payload()["data"][0]
    page = MetaPageInfo.from_graph(node)
    assert page.page_id == "1001"
    assert page.page_name == "Nile Coffee Roasters"
    assert page.category == "Coffee shop"
    assert page.instagram_business_account_id == "17841400000001"
    assert "MANAGE" in page.permissions
    assert page.page_access_token is not None
    assert page.page_access_token.get_secret_value() == "PAGE_TOKEN_1001"


def test_page_info_without_instagram() -> None:
    node = pages_payload()["data"][1]
    page = MetaPageInfo.from_graph(node)
    assert page.instagram_business_account_id is None
    assert page.permissions == ["ANALYZE", "CREATE_CONTENT"]


def test_page_info_permissions_coercion() -> None:
    assert MetaPageInfo(id="1", name="n", tasks=None).permissions == []
    assert MetaPageInfo(id="1", name="n", tasks="MANAGE").permissions == ["MANAGE"]
    assert MetaPageInfo(id="1", name="n").permissions == []
    assert MetaPageInfo(page_id="1", page_name="n", permissions=["A"]).permissions == ["A"]
    with pytest.raises(ValidationError):
        MetaPageInfo(id="1", name="n", tasks={"bad": "mapping"})


def test_page_info_dump_uses_explicit_field_names() -> None:
    page = MetaPageInfo.from_graph(pages_payload()["data"][0])
    dumped = page.model_dump()
    assert set(dumped) >= {"page_id", "page_name", "permissions", "page_access_token"}
    assert "id" not in dumped


def test_page_info_masks_page_token_in_json() -> None:
    page = MetaPageInfo.from_graph(pages_payload()["data"][0])
    dumped = page.model_dump_json()
    assert "PAGE_TOKEN_1001" not in dumped
    assert "PAGE_TOKEN_1001" not in repr(page)


def test_page_info_ignores_unknown_graph_fields() -> None:
    page = MetaPageInfo.from_graph({"id": "9", "name": "x", "fan_count": 10, "about": "..."})
    assert page.page_id == "9"


# ---------------------------------------------------------- ConnectionResult
def test_connection_result_serialises_without_plaintext_tokens() -> None:
    pages = [MetaPageInfo.from_graph(n) for n in pages_payload()["data"]]
    result = MetaConnectionResult(
        encrypted_user_token="gAAAAA...",
        token_expires_at=None,
        pages=pages,
        instagram_accounts=["17841400000001"],
    )
    dumped = result.model_dump_json()
    assert "PAGE_TOKEN" not in dumped
    assert result.provider == "meta"
    assert len(result.pages) == 2
