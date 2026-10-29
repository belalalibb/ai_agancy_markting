"""Unit tests — MetaConnectService orchestration (Graph mocked at HTTP level, fake repo).

SPRINT-02 note: the SPRINT-01 tests for ``build_authorize_url`` / ``connect`` /
``exchange_code`` / ``list_pages`` were rewritten for the new service surface
(``start_connection`` / ``complete_connection`` / ``list`` / ``get`` / ``disconnect``).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
import pytest
import respx
from src.core.config import Settings
from src.core.oauth_state import InvalidOAuthStateError, OAuthStateSigner
from src.core.security import TokenEncryptionService
from src.modules.social_connect.domain.models import AccountStatus
from src.modules.social_connect.exceptions import MetaTokenExpiredError
from src.modules.social_connect.meta_client import MetaGraphClient
from src.modules.social_connect.service import MetaConnectService

from tests.conftest import (
    FAKE_STATE_SECRET,
    debug_token_payload,
    oauth_error_payload,
    pages_payload,
    token_payload,
)
from tests.fakes import FakeConnectedAccountRepository

pytestmark = pytest.mark.contract


@pytest.fixture
def signer() -> OAuthStateSigner:
    return OAuthStateSigner(FAKE_STATE_SECRET)


@pytest.fixture
def service(
    meta_client: MetaGraphClient,
    encryption: TokenEncryptionService,
    fake_repo: FakeConnectedAccountRepository,
    signer: OAuthStateSigner,
    settings: Settings,
) -> MetaConnectService:
    return MetaConnectService(
        client=meta_client,
        encryption=encryption,
        repository=fake_repo,
        state_signer=signer,
        settings=settings,
    )


def _stub_oauth(graph_mock: respx.MockRouter) -> None:
    graph_mock.get("/oauth/access_token", params={"grant_type": "fb_exchange_token"}).mock(
        return_value=httpx.Response(200, json=token_payload("LONG_LIVED", 5183944))
    )
    graph_mock.get("/oauth/access_token").mock(
        return_value=httpx.Response(200, json=token_payload("SHORT_LIVED", 5000))
    )


def _stub_happy_path(graph_mock: respx.MockRouter) -> None:
    _stub_oauth(graph_mock)
    graph_mock.get("/debug_token").mock(
        return_value=httpx.Response(200, json=debug_token_payload())
    )
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, json=pages_payload()))


# --------------------------------------------------------------------- start
def test_start_connection_issues_signed_state(
    service: MetaConnectService, signer: OAuthStateSigner, settings: Settings
) -> None:
    result = service.start_connection("tenant-a")
    assert signer.verify(result.state) == "tenant-a"
    query = parse_qs(urlparse(result.authorization_url).query)
    assert query["state"] == [result.state]
    assert query["redirect_uri"] == [settings.meta_oauth_redirect_uri]
    assert query["scope"] == [settings.meta_oauth_scopes]


# ------------------------------------------------------------------ complete
async def test_complete_connection_happy_path_with_fake_repo(
    service: MetaConnectService,
    signer: OAuthStateSigner,
    graph_mock: respx.MockRouter,
    fake_repo: FakeConnectedAccountRepository,
    encryption: TokenEncryptionService,
) -> None:
    _stub_happy_path(graph_mock)
    account = await service.complete_connection(state=signer.issue("tenant-a"), code="CODE")

    assert fake_repo.upsert_calls == 1
    assert account.tenant_id == "tenant-a"
    assert account.provider == "meta"
    assert account.status is AccountStatus.ACTIVE
    assert account.encrypted_user_token != "LONG_LIVED"
    assert encryption.decrypt_token(account.encrypted_user_token) == "LONG_LIVED"
    assert [p.page_id for p in account.pages] == ["1001", "1002"]
    assert encryption.decrypt_token(account.pages[0].encrypted_page_token) == "PAGE_TOKEN_1001"
    assert account.pages[0].instagram is not None
    assert account.pages[0].instagram.ig_user_id == "17841400000001"
    assert account.pages[0].instagram.username == "nilecoffee"
    assert account.pages[1].instagram is None
    assert account.pages[0].tasks[:2] == ["ANALYZE", "ADVERTISE"]
    assert len(account.pages[0].tasks) == 5


async def test_complete_connection_uses_debug_token_for_identity_and_expiry(
    service: MetaConnectService, signer: OAuthStateSigner, graph_mock: respx.MockRouter
) -> None:
    _stub_oauth(graph_mock)
    graph_mock.get("/debug_token").mock(
        return_value=httpx.Response(
            200,
            json=debug_token_payload(
                user_id="777", scopes=["pages_show_list"], expires_at=1_900_000_000
            ),
        )
    )
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, json={"data": []}))

    account = await service.complete_connection(state=signer.issue("t"), code="CODE")
    assert account.provider_user_id == "777"
    assert account.granted_scopes == ["pages_show_list"]
    assert account.token_expires_at == datetime.fromtimestamp(1_900_000_000, tz=UTC)
    assert account.pages == []


async def test_complete_connection_debug_token_zero_expiry_means_unknown(
    service: MetaConnectService, signer: OAuthStateSigner, graph_mock: respx.MockRouter
) -> None:
    _stub_oauth(graph_mock)
    graph_mock.get("/debug_token").mock(
        return_value=httpx.Response(200, json=debug_token_payload(expires_at=0))
    )
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, json={"data": []}))
    account = await service.complete_connection(state=signer.issue("t"), code="CODE")
    assert account.token_expires_at is None  # D-055: unknown, not "never"


async def test_complete_connection_debug_token_failure_degrades_gracefully(
    service: MetaConnectService,
    signer: OAuthStateSigner,
    graph_mock: respx.MockRouter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _stub_oauth(graph_mock)
    graph_mock.get("/debug_token").mock(
        return_value=httpx.Response(
            400,
            json={
                "error": {"message": "Unsupported", "code": 100, "type": "GraphMethodException"}
            },
        )
    )
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, json=pages_payload()))

    with caplog.at_level(logging.WARNING):
        account = await service.complete_connection(state=signer.issue("t"), code="CODE")

    assert account.granted_scopes == []
    assert account.token_expires_at is None
    assert account.provider_user_id == "pages:1001,1002"  # deterministic fallback identity
    assert any("debug_token failed" in rec.message for rec in caplog.records)
    assert "LONG_LIVED" not in caplog.text  # the token is never logged


async def test_complete_connection_debug_token_190_is_fatal(
    service: MetaConnectService, signer: OAuthStateSigner, graph_mock: respx.MockRouter
) -> None:
    _stub_oauth(graph_mock)
    graph_mock.get("/debug_token").mock(
        return_value=httpx.Response(401, json=oauth_error_payload(code=190, subcode=463))
    )
    with pytest.raises(MetaTokenExpiredError):
        await service.complete_connection(state=signer.issue("t"), code="CODE")


async def test_complete_connection_without_pages_and_without_identity(
    service: MetaConnectService, signer: OAuthStateSigner, graph_mock: respx.MockRouter
) -> None:
    _stub_oauth(graph_mock)
    graph_mock.get("/debug_token").mock(
        return_value=httpx.Response(200, json={"data": {"is_valid": True}})
    )
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, json={"data": []}))
    account = await service.complete_connection(state=signer.issue("t"), code="CODE")
    assert account.provider_user_id == "unknown"
    assert account.pages == []


async def test_complete_connection_invalid_state_raises_before_any_graph_call(
    service: MetaConnectService, graph_mock: respx.MockRouter
) -> None:
    route = graph_mock.get("/oauth/access_token").mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(InvalidOAuthStateError):
        await service.complete_connection(state="garbage", code="CODE")
    assert route.call_count == 0


async def test_complete_connection_propagates_expired_token_error(
    service: MetaConnectService, signer: OAuthStateSigner, graph_mock: respx.MockRouter
) -> None:
    graph_mock.get("/oauth/access_token").mock(
        return_value=httpx.Response(400, json=oauth_error_payload())
    )
    with pytest.raises(MetaTokenExpiredError):
        await service.complete_connection(state=signer.issue("t"), code="BAD")


async def test_reconnect_same_user_upserts_not_duplicates(
    service: MetaConnectService,
    signer: OAuthStateSigner,
    graph_mock: respx.MockRouter,
    fake_repo: FakeConnectedAccountRepository,
) -> None:
    _stub_happy_path(graph_mock)
    first = await service.complete_connection(state=signer.issue("t"), code="C1")
    second = await service.complete_connection(state=signer.issue("t"), code="C2")
    assert first.id == second.id
    assert len(await service.list_connections("t")) == 1


async def test_page_without_access_token_is_stored_with_empty_ciphertext(
    service: MetaConnectService, signer: OAuthStateSigner, graph_mock: respx.MockRouter
) -> None:
    _stub_oauth(graph_mock)
    graph_mock.get("/debug_token").mock(
        return_value=httpx.Response(200, json=debug_token_payload())
    )
    graph_mock.get("/me/accounts").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "5", "name": "No token"}]})
    )
    account = await service.complete_connection(state=signer.issue("t"), code="CODE")
    assert account.pages[0].encrypted_page_token == ""


# --------------------------------------------------------------------- reads
async def test_list_get_disconnect_are_tenant_scoped(
    service: MetaConnectService, signer: OAuthStateSigner, graph_mock: respx.MockRouter
) -> None:
    _stub_happy_path(graph_mock)
    account = await service.complete_connection(state=signer.issue("tenant-a"), code="C")

    assert [a.id for a in await service.list_connections("tenant-a")] == [account.id]
    assert await service.list_connections("tenant-b") == []
    assert await service.get_connection("tenant-a", account.id) is not None
    assert await service.get_connection("tenant-b", account.id) is None
    assert await service.disconnect("tenant-b", account.id) is False
    assert await service.disconnect("tenant-a", account.id) is True
    assert await service.disconnect("tenant-a", account.id) is False
    assert await service.get_connection("tenant-a", uuid4()) is None


def test_decrypt_stored_token_roundtrip(
    service: MetaConnectService, encryption: TokenEncryptionService
) -> None:
    assert service.decrypt_stored_token(encryption.encrypt_token("abc")) == "abc"
