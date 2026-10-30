"""Unit tests — MetaConnectService orchestration (Graph mocked at HTTP level, fake repo).

SPRINT-02 note: the SPRINT-01 tests for ``build_authorize_url`` / ``connect`` /
``exchange_code`` / ``list_pages`` were rewritten for the new service surface
(``start_connection`` / ``complete_connection`` / ``list`` / ``get`` / ``disconnect``).
SPRINT-03: identities are UUIDs and connections are brand-scoped; the router verifies the
``state`` and authorises *before* calling ``complete_connection(tenant_id, brand_id, code)``.
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
from src.modules.social_connect.exceptions import (
    MetaGraphError,
    MetaOAuthError,
    MetaTokenExpiredError,
    MetaTokenForeignAppError,
    MetaTokenInvalidError,
)
from src.modules.social_connect.meta_client import MetaGraphClient
from src.modules.social_connect.service import MetaConnectService
from src.modules.tenancy.domain.access import BrandRole

from tests.conftest import (
    BRAND2_ID,
    BRAND_ID,
    FAKE_STATE_SECRET,
    OWNER_ID,
    debug_token_payload,
    me_payload,
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


def _stub_oauth(graph_mock: respx.MockRouter, *, me_id: str = "10001") -> None:
    graph_mock.post(
        "/oauth/access_token", data__contains={"grant_type": "fb_exchange_token"}
    ).mock(return_value=httpx.Response(200, json=token_payload("LONG_LIVED", 5183944)))
    graph_mock.post("/oauth/access_token").mock(
        return_value=httpx.Response(200, json=token_payload("SHORT_LIVED", 5000))
    )
    graph_mock.get("/me", params__contains={"fields": "id,name"}).mock(
        return_value=httpx.Response(200, json=me_payload(user_id=me_id))
    )


def _stub_happy_path(graph_mock: respx.MockRouter) -> None:
    _stub_oauth(graph_mock)
    graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(200, json=debug_token_payload())
    )
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, json=pages_payload()))


# --------------------------------------------------------------------- start
def test_start_connection_issues_signed_state(
    service: MetaConnectService, signer: OAuthStateSigner, settings: Settings
) -> None:
    result = service.start_connection(OWNER_ID, BRAND_ID)
    assert signer.verify(result.state) == (OWNER_ID, BRAND_ID)
    assert service.verify_state(result.state) == (OWNER_ID, BRAND_ID)
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
    account = await service.complete_connection(OWNER_ID, BRAND_ID, code="CODE")

    assert fake_repo.upsert_calls == 1
    assert account.brand_id == BRAND_ID
    assert account.connected_by_tenant_id == OWNER_ID
    assert account.provider == "meta"
    assert account.status is AccountStatus.ACTIVE
    assert account.encrypted_user_token != "LONG_LIVED"
    assert encryption.decrypt_token(account.encrypted_user_token) == "LONG_LIVED"
    assert [p.page_id for p in account.pages] == ["1001", "1002"]
    # B6: two Pages ⇒ metadata only, no Page token stored, nothing selected.
    assert all(p.encrypted_page_token is None and not p.is_selected for p in account.pages)
    fake_repo.assert_selection_invariant()
    assert account.pages[0].instagram is not None
    assert account.pages[0].instagram.ig_user_id == "17841400000001"
    assert account.pages[0].instagram.username == "nilecoffee"
    assert account.pages[1].instagram is None
    assert account.pages[0].tasks[:2] == ["ANALYZE", "ADVERTISE"]
    assert len(account.pages[0].tasks) == 5


async def test_complete_connection_uses_debug_token_for_scopes_and_expiry(
    service: MetaConnectService, signer: OAuthStateSigner, graph_mock: respx.MockRouter
) -> None:
    _stub_oauth(graph_mock, me_id="777")
    graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(
            200,
            json=debug_token_payload(
                user_id="777", scopes=["pages_show_list"], expires_at=1_900_000_000
            ),
        )
    )
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, json={"data": []}))

    account = await service.complete_connection(OWNER_ID, BRAND_ID, code="CODE")
    assert account.provider_user_id == "777"
    assert account.granted_scopes == ["pages_show_list"]
    assert account.token_expires_at == datetime.fromtimestamp(1_900_000_000, tz=UTC)
    assert account.pages == []


async def test_complete_connection_debug_token_zero_expiry_means_unknown(
    service: MetaConnectService, signer: OAuthStateSigner, graph_mock: respx.MockRouter
) -> None:
    _stub_oauth(graph_mock)
    graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(200, json=debug_token_payload(expires_at=0))
    )
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, json={"data": []}))
    account = await service.complete_connection(OWNER_ID, BRAND_ID, code="CODE")
    assert account.token_expires_at is None  # D-055: unknown, not "never"


async def test_complete_connection_debug_token_failure_keeps_identity_from_me(
    service: MetaConnectService,
    signer: OAuthStateSigner,
    graph_mock: respx.MockRouter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``/debug_token`` degradation only loses scopes/expiry — never the identity (F3)."""
    _stub_oauth(graph_mock)
    graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(
            400,
            json={
                "error": {"message": "Unsupported", "code": 100, "type": "GraphMethodException"}
            },
        )
    )
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, json=pages_payload()))

    with caplog.at_level(logging.WARNING):
        account = await service.complete_connection(OWNER_ID, BRAND_ID, code="CODE")

    assert account.granted_scopes == []
    assert account.token_expires_at is None
    assert account.provider_user_id == "10001"  # trusted identity from /me
    assert account.provider_user_name == "Nile Owner"
    # SPRINT-05 A-0: code 100 is a *misconfiguration* ⇒ ERROR (still degrades gracefully).
    assert any("debug_token misconfiguration" in rec.message for rec in caplog.records)
    assert "LONG_LIVED" not in caplog.text  # the token is never logged


async def test_complete_connection_debug_token_190_is_fatal(
    service: MetaConnectService, signer: OAuthStateSigner, graph_mock: respx.MockRouter
) -> None:
    _stub_oauth(graph_mock)
    graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(401, json=oauth_error_payload(code=190, subcode=463))
    )
    with pytest.raises(MetaTokenExpiredError):
        await service.complete_connection(OWNER_ID, BRAND_ID, code="CODE")


# ------------------------------------------------ SPRINT-02.1 identity (H3-H8)
async def test_complete_connection_uses_me_for_identity_and_name(
    service: MetaConnectService, signer: OAuthStateSigner, graph_mock: respx.MockRouter
) -> None:
    """H3 — ``provider_user_id``/``provider_user_name`` come from ``/me``, even when
    ``/debug_token`` omits ``user_id`` and there are no Pages at all."""
    _stub_oauth(graph_mock, me_id="424242")
    graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(200, json={"data": {"is_valid": True}})
    )
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, json={"data": []}))
    account = await service.complete_connection(OWNER_ID, BRAND_ID, code="CODE")
    assert account.provider_user_id == "424242"
    assert account.provider_user_name == "Nile Owner"
    assert account.pages == []


async def test_complete_connection_rejects_debug_is_valid_false(
    service: MetaConnectService,
    signer: OAuthStateSigner,
    graph_mock: respx.MockRouter,
    fake_repo: FakeConnectedAccountRepository,
) -> None:
    """H4 — ``is_valid: false`` is fatal and surfaces Meta's own error code."""
    _stub_oauth(graph_mock)
    graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(200, json=debug_token_payload(is_valid=False, error_code=190))
    )
    pages_route = graph_mock.get("/me/accounts").mock(
        return_value=httpx.Response(200, json=pages_payload())
    )
    with pytest.raises(MetaTokenInvalidError) as exc_info:
        await service.complete_connection(OWNER_ID, BRAND_ID, code="CODE")
    assert isinstance(exc_info.value, MetaOAuthError)
    assert exc_info.value.code == 190
    assert pages_route.call_count == 0
    assert fake_repo.upsert_calls == 0


async def test_complete_connection_rejects_debug_is_valid_false_defaults_code_190(
    service: MetaConnectService, signer: OAuthStateSigner, graph_mock: respx.MockRouter
) -> None:
    _stub_oauth(graph_mock)
    graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(200, json=debug_token_payload(is_valid=False))
    )
    with pytest.raises(MetaTokenInvalidError) as exc_info:
        await service.complete_connection(OWNER_ID, BRAND_ID, code="CODE")
    assert exc_info.value.code == 190


async def test_complete_connection_rejects_foreign_app_id(
    service: MetaConnectService,
    signer: OAuthStateSigner,
    graph_mock: respx.MockRouter,
    fake_repo: FakeConnectedAccountRepository,
) -> None:
    """H5 — a token issued to another Meta app is never persisted."""
    _stub_oauth(graph_mock)
    graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(200, json=debug_token_payload(app_id="999999999999999"))
    )
    with pytest.raises(MetaTokenForeignAppError, match="app_id"):
        await service.complete_connection(OWNER_ID, BRAND_ID, code="CODE")
    assert fake_repo.upsert_calls == 0


async def test_complete_connection_rejects_user_id_mismatch(
    service: MetaConnectService,
    signer: OAuthStateSigner,
    graph_mock: respx.MockRouter,
    fake_repo: FakeConnectedAccountRepository,
) -> None:
    """H6 — ``/debug_token.user_id`` must agree with ``/me.id``."""
    _stub_oauth(graph_mock, me_id="10001")
    graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(200, json=debug_token_payload(user_id="20002"))
    )
    with pytest.raises(MetaTokenForeignAppError, match="user_id does not match /me"):
        await service.complete_connection(OWNER_ID, BRAND_ID, code="CODE")
    assert fake_repo.upsert_calls == 0


async def test_complete_connection_get_me_failure_is_fatal(
    service: MetaConnectService,
    signer: OAuthStateSigner,
    graph_mock: respx.MockRouter,
    fake_repo: FakeConnectedAccountRepository,
) -> None:
    """H7 — without a trusted identity nothing is stored (no derived identity)."""
    graph_mock.post(
        "/oauth/access_token", data__contains={"grant_type": "fb_exchange_token"}
    ).mock(return_value=httpx.Response(200, json=token_payload("LONG_LIVED", 5183944)))
    graph_mock.post("/oauth/access_token").mock(
        return_value=httpx.Response(200, json=token_payload("SHORT_LIVED", 5000))
    )
    graph_mock.get("/me", params__contains={"fields": "id,name"}).mock(
        return_value=httpx.Response(
            400,
            json={
                "error": {"message": "Unsupported", "code": 100, "type": "GraphMethodException"}
            },
        )
    )
    debug_route = graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(200, json=debug_token_payload())
    )
    with pytest.raises(MetaGraphError):
        await service.complete_connection(OWNER_ID, BRAND_ID, code="CODE")
    assert debug_route.call_count == 0
    assert fake_repo.upsert_calls == 0


def test_no_fallback_identity_symbol_exists() -> None:
    """H8 — the derived-identity helper must stay deleted (SPRINT-02.1 F3)."""
    import src.modules.social_connect.service as service_module

    assert not hasattr(service_module, "_fallback_identity")
    assert "_fallback_identity" not in service_module.__dict__


async def test_verify_state_rejects_garbage_before_any_graph_call(
    service: MetaConnectService, graph_mock: respx.MockRouter
) -> None:
    route = graph_mock.post("/oauth/access_token").mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(InvalidOAuthStateError):
        service.verify_state("garbage")
    assert route.call_count == 0


async def test_complete_connection_propagates_expired_token_error(
    service: MetaConnectService, signer: OAuthStateSigner, graph_mock: respx.MockRouter
) -> None:
    graph_mock.post("/oauth/access_token").mock(
        return_value=httpx.Response(400, json=oauth_error_payload())
    )
    with pytest.raises(MetaTokenExpiredError):
        await service.complete_connection(OWNER_ID, BRAND_ID, code="BAD")


async def test_reconnect_same_user_upserts_not_duplicates(
    service: MetaConnectService,
    signer: OAuthStateSigner,
    graph_mock: respx.MockRouter,
    fake_repo: FakeConnectedAccountRepository,
) -> None:
    _stub_happy_path(graph_mock)
    first = await service.complete_connection(OWNER_ID, BRAND_ID, code="C1")
    second = await service.complete_connection(OWNER_ID, BRAND_ID, code="C2")
    assert first.id == second.id
    assert len(await service.list_connections(BRAND_ID)) == 1
    # B-U10: the same Meta user in another brand is a *separate* row.
    third = await service.complete_connection(OWNER_ID, BRAND2_ID, code="C3")
    assert third.id != first.id
    assert len(await service.list_connections(BRAND2_ID)) == 1
    assert len(fake_repo.rows) == 2


async def test_page_without_access_token_is_stored_as_none(
    service: MetaConnectService, signer: OAuthStateSigner, graph_mock: respx.MockRouter
) -> None:
    _stub_oauth(graph_mock)
    graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(200, json=debug_token_payload())
    )
    graph_mock.get("/me/accounts").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "5", "name": "No token"}]})
    )
    account = await service.complete_connection(OWNER_ID, BRAND_ID, code="CODE")
    assert account.pages[0].encrypted_page_token is None  # F4: NULL, never encrypt("")
    # A single Page would normally be auto-selected (B6) — but never without a token.
    assert account.pages[0].is_selected is False


# --------------------------------------------------------------------- reads
async def test_list_get_disconnect_are_brand_scoped(
    service: MetaConnectService, signer: OAuthStateSigner, graph_mock: respx.MockRouter
) -> None:
    _stub_happy_path(graph_mock)
    account = await service.complete_connection(OWNER_ID, BRAND_ID, code="C")

    assert [a.id for a in await service.list_connections(BRAND_ID)] == [account.id]
    assert await service.list_connections(BRAND2_ID) == []
    assert await service.get_connection(BRAND_ID, account.id) is not None
    assert await service.get_connection(BRAND2_ID, account.id) is None
    as_owner = {"actor_tenant_id": OWNER_ID, "actor_role": BrandRole.OWNER}
    assert await service.disconnect(BRAND2_ID, account.id, **as_owner) is False
    assert await service.disconnect(BRAND_ID, account.id, **as_owner) is True
    assert await service.disconnect(BRAND_ID, account.id, **as_owner) is False
    assert await service.get_connection(BRAND_ID, uuid4()) is None


# ------------------------------------------------------------ B6 selection
async def test_single_page_is_auto_selected_with_token(
    service: MetaConnectService,
    graph_mock: respx.MockRouter,
    encryption: TokenEncryptionService,
    fake_repo: FakeConnectedAccountRepository,
) -> None:
    _stub_oauth(graph_mock)
    graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(200, json=debug_token_payload())
    )
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, json=pages_payload(1)))
    account = await service.complete_connection(OWNER_ID, BRAND_ID, code="C")
    assert len(account.pages) == 1
    assert account.pages[0].is_selected is True
    assert encryption.decrypt_token(account.pages[0].encrypted_page_token) == "PAGE_TOKEN_1001"
    fake_repo.assert_selection_invariant()


async def test_select_pages_unknown_connection_returns_none(service: MetaConnectService) -> None:
    assert (
        await service.select_pages(BRAND_ID, uuid4(), ["1001"], actor_tenant_id=OWNER_ID) is None
    )


async def test_select_pages_dedupes_requested_ids(
    service: MetaConnectService,
    graph_mock: respx.MockRouter,
    fake_repo: FakeConnectedAccountRepository,
    encryption: TokenEncryptionService,
) -> None:
    _stub_oauth(graph_mock)
    graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(200, json=debug_token_payload())
    )
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, json=pages_payload(3)))
    account = await service.complete_connection(OWNER_ID, BRAND_ID, code="C")
    updated = await service.select_pages(
        BRAND_ID, account.id, ["1002", "1002"], actor_tenant_id=OWNER_ID
    )
    assert updated is not None
    by_id = {p.page_id: p for p in updated.pages}
    assert by_id["1002"].is_selected is True
    assert encryption.decrypt_token(by_id["1002"].encrypted_page_token) == "PAGE_TOKEN_1002"
    assert by_id["1001"].is_selected is False and by_id["1001"].encrypted_page_token is None
    assert by_id["1003"].is_selected is False and by_id["1003"].encrypted_page_token is None
    fake_repo.assert_selection_invariant()


def test_decrypt_stored_token_roundtrip(
    service: MetaConnectService, encryption: TokenEncryptionService
) -> None:
    assert service.decrypt_stored_token(encryption.encrypt_token("abc")) == "abc"


# ------------------------------------------------------------ SPRINT-05 A-0
async def test_a0_u2_debug_token_code_100_is_error_and_counted(
    service: MetaConnectService,
    graph_mock: respx.MockRouter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A0-U2 — code 100 (``GraphMethodException``) ⇒ ERROR + ``meta_debug_token_failures``.

    Degradation still happens (unknown scopes/expiry), but the misconfiguration is visible.
    """
    from src.core.metrics import counters

    counters.reset()
    _stub_oauth(graph_mock)
    graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(
            400,
            json={
                "error": {"message": "Unsupported", "code": 100, "type": "GraphMethodException"}
            },
        )
    )
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, json=pages_payload()))

    with caplog.at_level(logging.INFO):
        account = await service.complete_connection(OWNER_ID, BRAND_ID, code="CODE")

    assert account.granted_scopes == []
    assert account.token_expires_at is None
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "debug_token misconfiguration" in errors[0].getMessage()
    assert "graph code 100" in errors[0].getMessage()
    assert "LONG_LIVED" not in caplog.text
    assert counters["meta_debug_token_failures"] == 1


async def test_a0_u2_debug_token_transient_is_warning_and_not_counted(
    service: MetaConnectService,
    graph_mock: respx.MockRouter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A0-U2 (b) — ``MetaTransientError`` keeps the graceful WARNING, counter untouched."""
    from src.core.metrics import counters

    counters.reset()
    _stub_oauth(graph_mock)
    graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(
            500, json={"error": {"message": "Temporary", "code": 2, "type": "ApiException"}}
        )
    )
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, json=pages_payload()))

    with caplog.at_level(logging.INFO):
        account = await service.complete_connection(OWNER_ID, BRAND_ID, code="CODE")

    assert account.provider_user_id == "10001"
    assert not [r for r in caplog.records if r.levelno == logging.ERROR]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("debug_token failed" in r.getMessage() for r in warnings)
    assert counters["meta_debug_token_failures"] == 0


async def test_a0_u2_debug_token_generic_unclassified_error_is_counted(
    service: MetaConnectService, graph_mock: respx.MockRouter, caplog: pytest.LogCaptureFixture
) -> None:
    """A0-U2 (c) — an unclassified ``MetaGraphError`` (no code) is treated as misconfiguration."""
    from src.core.metrics import counters

    counters.reset()
    _stub_oauth(graph_mock)
    graph_mock.post("/debug_token").mock(
        return_value=httpx.Response(400, json={"error": {"message": "weird", "type": "Other"}})
    )
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, json=pages_payload()))
    with caplog.at_level(logging.ERROR):
        await service.complete_connection(OWNER_ID, BRAND_ID, code="CODE")
    assert counters["meta_debug_token_failures"] == 1
    assert "debug_token misconfiguration" in caplog.text


async def test_a0_u3_mark_reauth_required_without_marker_uses_repository(
    service: MetaConnectService,
    graph_mock: respx.MockRouter,
    fake_repo: FakeConnectedAccountRepository,
) -> None:
    """A0-U3 — bare service (no ``StatusMarker``): the 190 path falls back to
    ``repository.set_status`` (service.py fallback branch)."""
    _stub_happy_path(graph_mock)
    account = await service.complete_connection(OWNER_ID, BRAND_ID, code="CODE")
    assert account.status is AccountStatus.ACTIVE

    graph_mock.get("/me/accounts").mock(
        return_value=httpx.Response(400, json=oauth_error_payload("expired", code=190))
    )
    with pytest.raises(MetaTokenExpiredError):
        await service.select_pages(BRAND_ID, account.id, ["1001"], actor_tenant_id=OWNER_ID)
    assert fake_repo.rows[account.id].status is AccountStatus.REAUTH_REQUIRED
