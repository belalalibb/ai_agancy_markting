"""Unit tests — Pydantic contracts for the Meta connector."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from src.modules.social_connect.domain.models import ConnectedAccount, InstagramAsset, PageAsset
from src.modules.social_connect.schemas import (
    ConnectedAccountPublic,
    ConnectionStartRequest,
    InstagramAssetPublic,
    MetaAuthRequest,
    MetaPageInfo,
    MetaTokenResponse,
    PageAssetPublic,
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
    # SPRINT-02: the explicit "***" serializer was removed (Zero-Secret → fields are
    # simply absent from public schemas); SecretStr's own JSON masking still applies.
    assert json.loads(dumped)["long_lived_token"] != "LL_SECRET"
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


# ------------------------------------------------- Public views (SPRINT-02)
def _sample_account() -> ConnectedAccount:
    return ConnectedAccount(
        tenant_id="t1",
        provider_user_id="u1",
        encrypted_user_token="gAAAAA-user",
        granted_scopes=["pages_show_list"],
        pages=[
            PageAsset(
                page_id="1001",
                page_name="Nile",
                tasks=["ANALYZE"],
                encrypted_page_token="gAAAAA-page",
                instagram=InstagramAsset(ig_user_id="178", username="nile"),
            ),
            PageAsset(page_id="1002", page_name="Cairo", encrypted_page_token="gAAAAA-page2"),
        ],
    )


def test_public_schemas_have_no_token_fields() -> None:
    for model in (ConnectedAccountPublic, PageAssetPublic, InstagramAssetPublic):
        for name in model.model_fields:
            assert "token" not in name.lower(), f"{model.__name__}.{name}"
            assert "encrypted" not in name.lower(), f"{model.__name__}.{name}"


def test_connected_account_public_from_domain_drops_secrets() -> None:
    public = ConnectedAccountPublic.from_domain(_sample_account())
    dumped = public.model_dump_json()
    assert "gAAAAA" not in dumped
    assert "token" not in dumped.lower()
    assert "expires_at" in dumped
    assert public.pages[0].instagram is not None
    assert public.pages[0].instagram.username == "nile"
    assert public.pages[1].instagram is None
    assert public.provider == "meta"
    assert public.status == "active"


def test_page_info_from_graph_extracts_full_instagram_asset() -> None:
    page = MetaPageInfo.from_graph(
        {
            "id": "1",
            "name": "P",
            "instagram_business_account": {
                "id": "178",
                "username": "nile",
                "name": "Nile Coffee",
                "profile_picture_url": "https://cdn.example/p.jpg",
            },
        }
    )
    assert page.instagram_business_account_id == "178"
    assert page.instagram_username == "nile"
    assert page.instagram_name == "Nile Coffee"
    assert page.instagram_profile_picture_url == "https://cdn.example/p.jpg"


def test_connection_start_request_validation() -> None:
    assert ConnectionStartRequest(tenant_id="  demo ").tenant_id == "demo"
    with pytest.raises(ValidationError):
        ConnectionStartRequest(tenant_id="")
    with pytest.raises(ValidationError):
        ConnectionStartRequest(tenant_id="x", extra="no")  # type: ignore[call-arg]
