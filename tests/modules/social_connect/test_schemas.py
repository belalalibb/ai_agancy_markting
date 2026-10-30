"""Unit tests — Pydantic contracts for the Meta connector."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import get_args

import pytest
from pydantic import ValidationError
from src.modules.social_connect.domain.models import (
    AccountStatus,
    ConnectedAccount,
    InstagramAsset,
    PageAsset,
)
from src.modules.social_connect.schemas import (
    ConnectedAccountPublic,
    ConnectionStartRequest,
    InstagramAssetPublic,
    InstagramMediaPage,
    MetaAuthRequest,
    MetaDebugTokenInfo,
    MetaPageInfo,
    MetaTokenResponse,
    MetaUserInfo,
    PageAssetPublic,
    PagePostsPage,
    PageSelectionRequest,
    ResyncResponse,
    compute_token_health,
)
from src.modules.tenancy.schemas import BrandOut, BrandWithRoleOut, GrantOut, TenantOut

from tests.conftest import BRAND_ID, OWNER_ID, pages_payload

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
        brand_id=BRAND_ID,
        connected_by_tenant_id=OWNER_ID,
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


# Sanctioned exceptions: a derived boolean flag (SPRINT-02.1 F4) and the derived health
# *label* ``token_health`` (SPRINT-05 B1, a closed ``Literal``) — neither carries a token.
_ALLOWED_FLAG_FIELDS = frozenset({"has_page_token"})
_ALLOWED_LABEL_FIELDS = frozenset({"token_health"})
_TOKEN_HEALTH_VALUES = {"valid", "expiring_soon", "expired", "unknown", "reauth_required"}


_PUBLIC_MODELS = (
    ConnectedAccountPublic,
    PageAssetPublic,
    InstagramAssetPublic,
    # B11 — tenancy outputs join the Zero-Secret guard.
    TenantOut,
    BrandOut,
    BrandWithRoleOut,
    GrantOut,
)


def test_public_schemas_have_no_token_fields() -> None:
    for model in _PUBLIC_MODELS:
        for name, info in model.model_fields.items():
            if name in _ALLOWED_FLAG_FIELDS:
                assert info.annotation is bool, f"{model.__name__}.{name} must be a bool flag"
                continue
            if name in _ALLOWED_LABEL_FIELDS:
                assert set(get_args(info.annotation)) == _TOKEN_HEALTH_VALUES, (
                    f"{model.__name__}.{name} must be the closed TokenHealth Literal"
                )
                continue
            assert "token" not in name.lower(), f"{model.__name__}.{name}"
            assert "encrypted" not in name.lower(), f"{model.__name__}.{name}"


def test_connected_account_public_from_domain_drops_secrets() -> None:
    account = _sample_account()
    public = ConnectedAccountPublic.from_domain(
        account, viewer_tenant_id=account.connected_by_tenant_id
    )
    dumped = public.model_dump_json()
    assert "gAAAAA" not in dumped
    scrubbed = dumped.lower().replace("has_page_token", "").replace("token_health", "")
    assert "token" not in scrubbed
    assert "expires_at" in dumped
    assert str(BRAND_ID) in dumped
    assert str(OWNER_ID) in dumped
    assert "tenant_id" not in dumped.replace("connected_by_tenant_id", "")
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
    """SPRINT-04 B2 — the body is empty; identity comes from the JWT, so ``tenant_id`` is
    an *unknown* field and rejected (``extra="forbid"``)."""
    req = ConnectionStartRequest()
    assert req.model_dump() == {}
    assert "tenant_id" not in ConnectionStartRequest.model_fields
    with pytest.raises(ValidationError):
        ConnectionStartRequest(tenant_id=str(OWNER_ID))  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        ConnectionStartRequest(extra="no")  # type: ignore[call-arg]


def test_page_selection_request_validation() -> None:
    """B6 — page ids: 1..50, non-empty, unique; SPRINT-04 B2: no ``tenant_id`` in the body."""
    req = PageSelectionRequest(page_ids=["1001", "1002"])
    assert req.page_ids == ["1001", "1002"]
    assert "tenant_id" not in PageSelectionRequest.model_fields
    with pytest.raises(ValidationError):
        PageSelectionRequest(page_ids=[])
    with pytest.raises(ValidationError):
        PageSelectionRequest(page_ids=["1001", "1001"])
    with pytest.raises(ValidationError):
        PageSelectionRequest(page_ids=[" "])
    with pytest.raises(ValidationError):
        PageSelectionRequest(tenant_id=str(OWNER_ID), page_ids=["1001"])  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        PageSelectionRequest(page_ids=[str(i) for i in range(51)])


def test_page_asset_public_has_page_token_flag() -> None:
    """H9 — ``has_page_token`` is derived from ``encrypted_page_token`` and leaks nothing."""
    with_token = PageAssetPublic.from_domain(
        PageAsset(page_id="1", page_name="A", encrypted_page_token="gAAAAA-cipher")
    )
    without_token = PageAssetPublic.from_domain(
        PageAsset(page_id="2", page_name="B", encrypted_page_token=None)
    )
    assert with_token.has_page_token is True
    assert without_token.has_page_token is False
    assert "gAAAAA" not in with_token.model_dump_json()
    assert "encrypted" not in with_token.model_dump_json()
    # Default ``PageAsset`` (no token supplied at all) also maps to False.
    assert (
        PageAssetPublic.from_domain(PageAsset(page_id="3", page_name="C")).has_page_token is False
    )


def test_meta_user_info_and_debug_error_code_schema() -> None:
    assert MetaUserInfo(id="1").name is None
    assert MetaUserInfo(id="1", name="N").name == "N"
    info = MetaDebugTokenInfo(app_id="a", is_valid=False, error_code=190)
    assert info.error_code == 190
    assert MetaDebugTokenInfo(app_id="a", is_valid=True).error_code is None


# ------------------------------------------------------------ SPRINT-05 B1-U1
_NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("status", "expires_at", "expected"),
    [
        (AccountStatus.REAUTH_REQUIRED, _NOW + timedelta(days=60), "reauth_required"),
        (AccountStatus.ACTIVE, None, "unknown"),
        (AccountStatus.ACTIVE, _NOW - timedelta(seconds=1), "expired"),
        (AccountStatus.ACTIVE, _NOW + timedelta(days=7), "expiring_soon"),
        (AccountStatus.ACTIVE, _NOW + timedelta(days=7, seconds=1), "valid"),
    ],
)
def test_b1_u1_token_health_five_cases_with_injected_now(
    status: AccountStatus, expires_at: datetime | None, expected: str
) -> None:
    """B1-U1 — ``token_health`` from ``status`` + ``expires_at`` at an injected ``now``;
    ``reauth_required`` wins even over a far-future expiry; exactly 7 days is still
    ``expiring_soon``; ``last_synced_at`` is exposed."""
    synced = _NOW - timedelta(hours=1)
    account = _sample_account().model_copy(
        update={"status": status, "token_expires_at": expires_at, "last_synced_at": synced}
    )
    public = ConnectedAccountPublic.from_domain(account, viewer_tenant_id=OWNER_ID, now=_NOW)
    assert public.token_health == expected
    assert public.last_synced_at == synced
    assert compute_token_health(account, now=_NOW) == expected


def test_b1_u1_token_health_defaults_to_wall_clock() -> None:
    far = _sample_account().model_copy(
        update={"token_expires_at": datetime(2099, 1, 1, tzinfo=UTC)}
    )
    assert (
        ConnectedAccountPublic.from_domain(far, viewer_tenant_id=OWNER_ID).token_health == "valid"
    )
    assert public_schema_has_no_paging_next()


def public_schema_has_no_paging_next() -> bool:
    """§2-0(a): the cursor containers only know ``next_cursor`` / ``has_more`` / ``items``."""
    for model in (PagePostsPage, InstagramMediaPage):
        assert set(model.model_fields) == {"next_cursor", "has_more", "items"}
    return True


def test_b2_resync_response_carries_removed_ids_and_nothing_secret() -> None:
    account = _sample_account()
    res = ResyncResponse.from_resync(
        account, viewer_tenant_id=OWNER_ID, removed_selected_page_ids=["1003"], now=_NOW
    )
    dumped = res.model_dump_json()
    assert res.removed_selected_page_ids == ["1003"]
    assert res.token_health == "unknown"
    assert "gAAAAA" not in dumped
    assert "removed_selected_page_ids" not in ConnectedAccountPublic.model_fields
