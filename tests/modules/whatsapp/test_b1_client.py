"""SPRINT-09 B-1 — ``WhatsAppCloudClient`` (B1-U6), ``connect_account`` (B1-U7) and the
D-069 learning-signals column guard (B1-U8)."""

from __future__ import annotations

import json
import logging

import httpx
import pytest
import respx
from src.core.config import Settings
from src.core.security import TokenEncryptionService
from src.modules.whatsapp.client import (
    WhatsAppCloudClient,
    map_cloud_error,
    mask_phone,
)
from src.modules.whatsapp.exceptions import (
    AccountAlreadyExistsError,
    AccountNotFoundError,
    CredentialsInvalidError,
    WhatsAppAmbiguousError,
    WhatsAppAuthError,
    WhatsAppPermanentError,
    WhatsAppRateLimitedError,
    WhatsAppRecipientError,
    WhatsAppTransientError,
)
from src.modules.whatsapp.service import (
    PostgresWhatsAppTokenProvider,
    WhatsAppAccountService,
    hash_verify_token,
)

from tests.conftest import BRAND_ID, OWNER_ID, USER_BY_TENANT
from tests.fakes import FakeWhatsAppRepository
from tests.modules.whatsapp.conftest import (
    APP_SECRET,
    CUSTOMER_WA_ID,
    PHONE_NUMBER_ID,
    SYSTEM_TOKEN,
    VERIFY_TOKEN,
    make_account,
)

pytestmark = pytest.mark.contract

MESSAGES = f"/{PHONE_NUMBER_ID}/messages"


@pytest.fixture
async def wa_client(settings: Settings):  # noqa: ANN201
    async def _no_sleep(_: float) -> None:
        return None

    async with httpx.AsyncClient() as http:
        yield WhatsAppCloudClient(settings=settings, http_client=http, sleep=_no_sleep)


def _ok(wamid: str = "wamid.OUT0001") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "messaging_product": "whatsapp",
            "contacts": [{"input": CUSTOMER_WA_ID, "wa_id": CUSTOMER_WA_ID}],
            "messages": [{"id": wamid}],
        },
    )


def _graph_error(code: int, http_status: int = 400, **extra: object) -> httpx.Response:
    return httpx.Response(
        http_status,
        json={
            "error": {
                "message": "Provider text that must never leak",
                "type": "OAuthException",
                "code": code,
                "fbtrace_id": "TRACE1",
                **extra,
            }
        },
    )


# ---------------------------------------------------------------------- B1-U6 ---
async def test_b1_u6_send_text_request_shape_bearer_only_and_single_post(
    wa_client: WhatsAppCloudClient,
    graph_mock: respx.MockRouter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    route = graph_mock.post(MESSAGES).mock(return_value=_ok())
    with caplog.at_level(logging.INFO):
        sent = await wa_client.send_text(SYSTEM_TOKEN, PHONE_NUMBER_ID, CUSTOMER_WA_ID, "مرحباً")
    assert sent.wamid == "wamid.OUT0001"
    assert len(route.calls) == 1
    request = route.calls[0].request
    assert request.headers["Authorization"] == f"Bearer {SYSTEM_TOKEN}"
    assert SYSTEM_TOKEN not in str(request.url)  # D-064: never in the URL
    assert json.loads(request.content) == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": CUSTOMER_WA_ID,
        "type": "text",
        "text": {"preview_url": False, "body": "مرحباً"},
    }
    # §2-0 ج: the log shows a masked phone, kind and length — never the text or the token.
    assert "مرحباً" not in caplog.text and SYSTEM_TOKEN not in caplog.text
    assert "…7890" in caplog.text and "kind=text" in caplog.text and "len=6" in caplog.text
    assert CUSTOMER_WA_ID not in caplog.text


async def test_b1_u6_send_template_shape(
    wa_client: WhatsAppCloudClient, graph_mock: respx.MockRouter
) -> None:
    route = graph_mock.post(MESSAGES).mock(return_value=_ok("wamid.TPL"))
    sent = await wa_client.send_template(
        SYSTEM_TOKEN,
        PHONE_NUMBER_ID,
        CUSTOMER_WA_ID,
        "order_update",
        "ar",
        components=[{"type": "body", "parameters": [{"type": "text", "text": "123"}]}],
    )
    assert sent.wamid == "wamid.TPL" and len(route.calls) == 1
    body = json.loads(route.calls[0].request.content)
    assert body["type"] == "template"
    assert body["template"] == {
        "name": "order_update",
        "language": {"code": "ar"},
        "components": [{"type": "body", "parameters": [{"type": "text", "text": "123"}]}],
    }
    plain = graph_mock.post(MESSAGES).mock(return_value=_ok("wamid.TPL2"))
    await wa_client.send_template(SYSTEM_TOKEN, PHONE_NUMBER_ID, CUSTOMER_WA_ID, "hello", "en")
    assert "components" not in json.loads(plain.calls[-1].request.content)["template"]


async def test_b1_u6_mark_read_shape(
    wa_client: WhatsAppCloudClient, graph_mock: respx.MockRouter
) -> None:
    route = graph_mock.post(MESSAGES).mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    await wa_client.mark_read(SYSTEM_TOKEN, PHONE_NUMBER_ID, "wamid.IN1")
    assert json.loads(route.calls[0].request.content) == {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": "wamid.IN1",
    }


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(502, json={}),
        httpx.Response(500, json={"error": {"message": "x", "code": 1}}),
        _graph_error(131000, http_status=500),
    ],
    ids=["502-empty", "500-code-1", "500-131000"],
)
async def test_b1_u6_post_5xx_is_ambiguous_and_never_retried(
    wa_client: WhatsAppCloudClient, graph_mock: respx.MockRouter, response: httpx.Response
) -> None:
    route = graph_mock.post(MESSAGES).mock(side_effect=[response, _ok()])
    with pytest.raises(WhatsAppAmbiguousError) as info:
        await wa_client.send_text(SYSTEM_TOKEN, PHONE_NUMBER_ID, CUSTOMER_WA_ID, "hi")
    assert len(route.calls) == 1  # D-070
    assert "Provider text" not in str(info.value)


@pytest.mark.parametrize(
    "exc", [httpx.ReadTimeout("slow"), httpx.WriteTimeout("w"), httpx.RemoteProtocolError("eof")]
)
async def test_b1_u6_post_transport_failure_after_send_is_ambiguous(
    wa_client: WhatsAppCloudClient, graph_mock: respx.MockRouter, exc: Exception
) -> None:
    route = graph_mock.post(MESSAGES).mock(side_effect=exc)
    with pytest.raises(WhatsAppAmbiguousError):
        await wa_client.send_text(SYSTEM_TOKEN, PHONE_NUMBER_ID, CUSTOMER_WA_ID, "hi")
    assert len(route.calls) == 1


@pytest.mark.parametrize("exc", [httpx.ConnectError("refused"), httpx.ConnectTimeout("hs")])
async def test_b1_u6_post_connect_failure_is_transient_single_call(
    wa_client: WhatsAppCloudClient, graph_mock: respx.MockRouter, exc: Exception
) -> None:
    route = graph_mock.post(MESSAGES).mock(side_effect=exc)
    with pytest.raises(WhatsAppTransientError):
        await wa_client.send_text(SYSTEM_TOKEN, PHONE_NUMBER_ID, CUSTOMER_WA_ID, "hi")
    assert len(route.calls) == 1


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (_graph_error(130429, http_status=400), WhatsAppRateLimitedError),
        (_graph_error(80007, http_status=400), WhatsAppRateLimitedError),
        (httpx.Response(429, json={}, headers={"Retry-After": "7"}), WhatsAppRateLimitedError),
        (_graph_error(131026), WhatsAppRecipientError),
        (_graph_error(131047), WhatsAppRecipientError),
        (_graph_error(131051), WhatsAppRecipientError),
        (_graph_error(190, http_status=401), WhatsAppAuthError),
        (_graph_error(10, http_status=403), WhatsAppAuthError),
        (_graph_error(100), WhatsAppPermanentError),
        (httpx.Response(400, text="not json"), WhatsAppPermanentError),
    ],
    ids=[
        "130429",
        "80007",
        "http-429",
        "131026",
        "131047",
        "131051",
        "190",
        "10",
        "100",
        "non-json-400",
    ],
)
async def test_b1_u6_error_code_mapping_single_call(
    wa_client: WhatsAppCloudClient,
    graph_mock: respx.MockRouter,
    response: httpx.Response,
    expected: type[Exception],
) -> None:
    route = graph_mock.post(MESSAGES).mock(side_effect=[response, _ok()])
    with pytest.raises(expected) as info:
        await wa_client.send_text(SYSTEM_TOKEN, PHONE_NUMBER_ID, CUSTOMER_WA_ID, "hi")
    assert len(route.calls) == 1  # rate limits included: never retried
    assert "Provider text" not in str(info.value)
    if isinstance(info.value, WhatsAppRateLimitedError) and response.status_code == 429:
        assert info.value.retry_after_seconds == 7.0


async def test_b1_u6_success_without_wamid_is_ambiguous(
    wa_client: WhatsAppCloudClient, graph_mock: respx.MockRouter
) -> None:
    graph_mock.post(MESSAGES).mock(return_value=httpx.Response(200, json={"messages": []}))
    with pytest.raises(WhatsAppAmbiguousError):
        await wa_client.send_text(SYSTEM_TOKEN, PHONE_NUMBER_ID, CUSTOMER_WA_ID, "hi")


async def test_b1_u6_describe_phone_number_get_is_retried_then_parsed(
    wa_client: WhatsAppCloudClient, graph_mock: respx.MockRouter
) -> None:
    route = graph_mock.get(f"/{PHONE_NUMBER_ID}").mock(
        side_effect=[
            httpx.Response(503, json={}),
            httpx.ReadTimeout("slow"),
            httpx.Response(
                200, json={"display_phone_number": "+1 555 000 1111", "verified_name": "Nile"}
            ),
        ]
    )
    info = await wa_client.describe_phone_number(SYSTEM_TOKEN, PHONE_NUMBER_ID)
    assert info.display_phone_number == "+1 555 000 1111" and info.verified_name == "Nile"
    assert len(route.calls) == 3
    assert route.calls[0].request.headers["Authorization"] == f"Bearer {SYSTEM_TOKEN}"
    assert route.calls[0].request.url.params["fields"] == "display_phone_number,verified_name"


async def test_b1_u6_describe_phone_number_exhausts_retries_and_maps_errors(
    wa_client: WhatsAppCloudClient, graph_mock: respx.MockRouter
) -> None:
    timeouts = graph_mock.get(f"/{PHONE_NUMBER_ID}").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(WhatsAppTransientError):
        await wa_client.describe_phone_number(SYSTEM_TOKEN, PHONE_NUMBER_ID)
    assert len(timeouts.calls) == 3  # META_HTTP_MAX_RETRIES=2 ⇒ 3 attempts
    graph_mock.get(f"/{PHONE_NUMBER_ID}").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(WhatsAppTransientError):
        await wa_client.describe_phone_number(SYSTEM_TOKEN, PHONE_NUMBER_ID)
    graph_mock.get(f"/{PHONE_NUMBER_ID}").mock(return_value=_graph_error(190, http_status=401))
    with pytest.raises(WhatsAppAuthError):
        await wa_client.describe_phone_number(SYSTEM_TOKEN, PHONE_NUMBER_ID)
    graph_mock.get(f"/{PHONE_NUMBER_ID}").mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(WhatsAppPermanentError):
        await wa_client.describe_phone_number(SYSTEM_TOKEN, PHONE_NUMBER_ID)


def test_b1_u6_helpers() -> None:
    assert mask_phone("201234567890") == "…7890"
    assert mask_phone("+1 (555) 000-1111") == "…1111"
    assert mask_phone(None) == "…" and mask_phone("abc") == "…"
    err = map_cloud_error({"error": {"code": "x", "fbtrace_id": 5}}, 400, retry_after=None)
    assert isinstance(err, WhatsAppPermanentError) and err.code is None and err.fbtrace_id is None
    assert "http=400" in str(err)
    rl = map_cloud_error({}, 429, retry_after=3.0)
    assert isinstance(rl, WhatsAppRateLimitedError) and rl.retry_after_seconds == 3.0


def test_b1_u6_client_builds_default_http_client(settings: Settings) -> None:
    client = WhatsAppCloudClient(settings=settings)
    assert client.base_url == settings.meta_graph_url


# ---------------------------------------------------------------------- B1-U7 ---
async def test_b1_u7_connect_account_verifies_token_then_encrypts_and_stores(
    wa_client: WhatsAppCloudClient,
    graph_mock: respx.MockRouter,
    encryption: TokenEncryptionService,
) -> None:
    repo = FakeWhatsAppRepository()
    probe = graph_mock.get(f"/{PHONE_NUMBER_ID}").mock(
        return_value=httpx.Response(
            200, json={"display_phone_number": "+1 555 000 1111", "verified_name": "Nile"}
        )
    )
    service = WhatsAppAccountService(repo, wa_client, encryption)
    account = await service.connect_account(
        BRAND_ID,
        USER_BY_TENANT[OWNER_ID],
        phone_number_id=PHONE_NUMBER_ID,
        waba_id="WABA_ID_1",
        display_phone="",
        system_token=SYSTEM_TOKEN,
        app_secret=APP_SECRET,
        verify_token=VERIFY_TOKEN,
    )
    assert len(probe.calls) == 1
    assert account.display_phone == "+1 555 000 1111"  # filled from Graph when empty
    stored = repo.accounts[account.id]
    assert stored.encrypted_system_token != SYSTEM_TOKEN
    assert encryption.decrypt_token(stored.encrypted_system_token) == SYSTEM_TOKEN
    assert encryption.decrypt_token(stored.encrypted_app_secret) == APP_SECRET
    assert stored.verify_token_hash == hash_verify_token(VERIFY_TOKEN)
    assert VERIFY_TOKEN not in stored.verify_token_hash
    # D-064: secrets are repr=False / excluded from serialisation.
    assert SYSTEM_TOKEN not in repr(stored) and "encrypted_system_token" not in repr(stored)
    assert "encrypted_system_token" not in stored.model_dump()
    assert "encrypted_app_secret" not in stored.model_dump_json()
    # Second account for the same brand ⇒ 409-class error, no Graph call.
    with pytest.raises(AccountAlreadyExistsError):
        await service.connect_account(
            BRAND_ID,
            USER_BY_TENANT[OWNER_ID],
            phone_number_id="PNID_OTHER",
            waba_id="W",
            display_phone="x",
            system_token="t",  # noqa: S106 - test literal
            app_secret="s",  # noqa: S106 - test literal
            verify_token="v",  # noqa: S106 - test literal
        )
    assert len(probe.calls) == 1
    assert (await service.get_account(BRAND_ID)).id == account.id
    disabled = await service.disable_account(BRAND_ID)
    assert disabled.status.value == "disabled"
    assert repo.accounts[account.id].status.value == "disabled"


@pytest.mark.parametrize(
    "response",
    [_graph_error(190, http_status=401), _graph_error(100), httpx.Response(503, json={})],
    ids=["190", "100", "503"],
)
async def test_b1_u7_connect_account_with_rejected_token_is_422_and_writes_no_row(
    wa_client: WhatsAppCloudClient,
    graph_mock: respx.MockRouter,
    encryption: TokenEncryptionService,
    response: httpx.Response,
) -> None:
    repo = FakeWhatsAppRepository()
    graph_mock.get(f"/{PHONE_NUMBER_ID}").mock(return_value=response)
    service = WhatsAppAccountService(repo, wa_client, encryption)
    with pytest.raises(CredentialsInvalidError) as info:
        await service.connect_account(
            BRAND_ID,
            USER_BY_TENANT[OWNER_ID],
            phone_number_id=PHONE_NUMBER_ID,
            waba_id="WABA_ID_1",
            display_phone="+1",
            system_token=SYSTEM_TOKEN,
            app_secret=APP_SECRET,
            verify_token=VERIFY_TOKEN,
        )
    assert info.value.error == "whatsapp_credentials_invalid"
    assert repo.accounts == {}
    with pytest.raises(AccountNotFoundError):
        await service.get_account(BRAND_ID)


async def test_b1_u7_token_provider_resolves_only_active_accounts(
    encryption: TokenEncryptionService,
) -> None:
    from datetime import UTC, datetime

    from src.modules.whatsapp.domain.models import AccountStatus

    repo = FakeWhatsAppRepository()
    account = await repo.create_account(make_account(encryption))
    provider = PostgresWhatsAppTokenProvider(repo, encryption)
    target = await provider.resolve(account.id)
    assert target is not None
    assert target.token == SYSTEM_TOKEN and target.app_secret == APP_SECRET
    assert target.phone_number_id == PHONE_NUMBER_ID and target.brand_id == BRAND_ID
    assert SYSTEM_TOKEN not in repr(target) and APP_SECRET not in repr(target)  # repr=False
    by_phone = await provider.resolve_by_phone_number_id(PHONE_NUMBER_ID)
    assert by_phone is not None and by_phone.account_id == account.id
    assert await provider.resolve_by_phone_number_id("PNID_NOBODY") is None
    await repo.set_account_status(account.id, AccountStatus.DISABLED, now=datetime.now(tz=UTC))
    assert await provider.resolve(account.id) is None
    assert await provider.resolve_by_phone_number_id(PHONE_NUMBER_ID) is None


# ---------------------------------------------------------------------- B1-U8 ---
def test_b1_u8_learning_signals_have_no_text_column() -> None:
    """D-069 guard — neither the ORM row nor migration 0009 defines a text-bearing column on
    ``wa_learning_signals`` (features only)."""
    import re
    from pathlib import Path

    from src.modules.whatsapp.infrastructure.orm import LearningSignalRow

    columns = {c.name for c in LearningSignalRow.__table__.columns}
    assert columns == {
        "id",
        "brand_id",
        "reply_draft_id",
        "signal",
        "edit_distance_ratio",
        "rejection_reason",
        "prompt_version",
        "model",
        "credential_mode",
        "features",
        "created_at",
    }
    forbidden = {"body", "text", "suggested_body", "final_body", "message", "content", "prompt"}
    assert not (columns & forbidden)
    for column in LearningSignalRow.__table__.columns:
        if column.name in {
            "signal",
            "rejection_reason",
            "prompt_version",
            "model",
            "credential_mode",
        }:
            assert column.type.length <= 64  # short labels, never free text
        assert type(column.type).__name__ != "Text"
    source = Path("alembic/versions/0009_whatsapp.py").read_text(encoding="utf-8")
    block = source[source.index('"wa_learning_signals"') :]
    block = block[: block.index("def downgrade")]
    assert "sa.Text()" not in block
    assert not re.search(r"\"(body|text|suggested_body|final_body|message|content)\"", block)
