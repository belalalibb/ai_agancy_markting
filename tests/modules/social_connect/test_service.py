"""Unit tests — MetaConnectService orchestration (client mocked at HTTP level)."""

from __future__ import annotations

import httpx
import pytest
import respx
from src.core.security import TokenEncryptionService
from src.modules.social_connect.exceptions import MetaTokenExpiredError
from src.modules.social_connect.meta_client import MetaGraphClient
from src.modules.social_connect.schemas import MetaAuthRequest
from src.modules.social_connect.service import MetaConnectService

from tests.conftest import oauth_error_payload, pages_payload, token_payload

pytestmark = pytest.mark.contract


@pytest.fixture
def service(
    meta_client: MetaGraphClient, encryption: TokenEncryptionService
) -> MetaConnectService:
    return MetaConnectService(client=meta_client, encryption=encryption)


def _stub_oauth(graph_mock: respx.MockRouter) -> None:
    graph_mock.get("/oauth/access_token", params={"grant_type": "fb_exchange_token"}).mock(
        return_value=httpx.Response(200, json=token_payload("LONG_LIVED", 5183944))
    )
    graph_mock.get("/oauth/access_token").mock(
        return_value=httpx.Response(200, json=token_payload("SHORT_LIVED", 5000))
    )


def test_build_authorize_url(service: MetaConnectService) -> None:
    result = service.build_authorize_url("https://app.example.com/cb", state="abc")
    assert result.state == "abc"
    assert "state=abc" in result.authorize_url


async def test_connect_full_flow_returns_encrypted_token_and_pages(
    service: MetaConnectService,
    graph_mock: respx.MockRouter,
    encryption: TokenEncryptionService,
) -> None:
    _stub_oauth(graph_mock)
    pages_route = graph_mock.get("/me/accounts").mock(
        return_value=httpx.Response(200, json=pages_payload())
    )

    result = await service.connect(
        MetaAuthRequest(code="AUTH_CODE", redirect_uri="https://app.example.com/cb")
    )

    # Pages fetched with the *long-lived* token
    assert dict(pages_route.calls[0].request.url.params)["access_token"] == "LONG_LIVED"
    # Token is encrypted and round-trips
    assert result.encrypted_user_token != "LONG_LIVED"
    assert "LONG_LIVED" not in result.encrypted_user_token
    assert encryption.decrypt_token(result.encrypted_user_token) == "LONG_LIVED"
    assert service.decrypt_stored_token(result.encrypted_user_token) == "LONG_LIVED"
    # Pages / IG accounts
    assert [p.page_id for p in result.pages] == ["1001", "1002"]
    assert result.instagram_accounts == ["17841400000001"]
    assert result.token_expires_at is not None
    assert result.provider == "meta"


async def test_connect_propagates_expired_token_error(
    service: MetaConnectService, graph_mock: respx.MockRouter
) -> None:
    _stub_oauth(graph_mock)
    graph_mock.get("/me/accounts").mock(
        return_value=httpx.Response(400, json=oauth_error_payload(code=190, subcode=463))
    )
    with pytest.raises(MetaTokenExpiredError):
        await service.connect(MetaAuthRequest(code="C", redirect_uri="https://a.b/c"))


async def test_exchange_code_only(
    service: MetaConnectService, graph_mock: respx.MockRouter
) -> None:
    _stub_oauth(graph_mock)
    token = await service.exchange_code(MetaAuthRequest(code="C", redirect_uri="https://a.b/c"))
    assert token.long_lived_token.get_secret_value() == "LONG_LIVED"


async def test_list_pages_only(service: MetaConnectService, graph_mock: respx.MockRouter) -> None:
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, json=pages_payload()))
    pages = await service.list_pages("T")
    assert len(pages) == 2
