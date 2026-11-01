"""SPRINT-09 B-2 — unified inbox routes (B2-U1..U5) on the fake repository + respx."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.core.metrics import counters
from src.modules.tenancy.domain.models import GrantRole
from src.modules.whatsapp.domain.models import (
    AccountStatus,
    ConversationStatus,
    DeliveryStatus,
    Direction,
    Message,
    MessageKind,
)
from src.modules.whatsapp.schemas import MASKED_WA_ID

from tests.conftest import AGENCY_ID, BRAND_ID, OWNER_ID, STRANGER_ID, USER_BY_TENANT, grant
from tests.fakes import FakeStore, FakeWhatsAppRepository
from tests.modules.whatsapp.conftest import (
    APP_SECRET,
    CUSTOMER_WA_ID,
    PHONE_NUMBER_ID,
    SYSTEM_TOKEN,
    VERIFY_TOKEN,
)

API = "/api/v1"
BASE = f"{API}/brands/{BRAND_ID}/whatsapp"
MESSAGES = f"/{PHONE_NUMBER_ID}/messages"


def _ok(wamid: str = "wamid.OUT1") -> httpx.Response:
    return httpx.Response(200, json={"messaging_product": "whatsapp", "messages": [{"id": wamid}]})


def _graph_error(code: int, http_status: int = 400) -> httpx.Response:
    return httpx.Response(
        http_status, json={"error": {"message": "leak?", "code": code, "fbtrace_id": "t"}}
    )


async def _seed_conversation(
    repo: FakeWhatsAppRepository,
    account_id: UUID,
    *,
    inbound_ago: timedelta = timedelta(hours=1),
    wa_id: str = CUSTOMER_WA_ID,
    body: str = "مرحبا",
):
    at = datetime.now(tz=UTC) - inbound_ago
    conv = await repo.upsert_conversation_inbound(
        brand_id=BRAND_ID,
        account_id=account_id,
        wa_id=wa_id,
        profile_name="Nour",
        at=at,
        window_expires_at=at + timedelta(hours=24),
    )
    await repo.insert_inbound_if_new(
        Message(
            conversation_id=conv.id,
            brand_id=BRAND_ID,
            direction=Direction.INBOUND,
            wamid=f"wamid.IN.{uuid4()}",
            kind=MessageKind.TEXT,
            body=body,
            delivery_status=DeliveryStatus.RECEIVED,
            platform_timestamp=at,
            created_at=at,
        )
    )
    return conv


@pytest.fixture
def roles(store: FakeStore) -> None:
    grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    grant(store, BRAND_ID, STRANGER_ID, GrantRole.VIEWER)


# ---------------------------------------------------------------------- B2-U1 ---
def test_b2_u1_account_lifecycle_roles_and_no_secret_in_responses(
    wa_app: FastAPI,
    wa_repo: FakeWhatsAppRepository,
    auth_headers,
    roles: None,
    graph_mock: respx.MockRouter,
) -> None:
    graph_mock.get(f"/{PHONE_NUMBER_ID}").mock(
        return_value=httpx.Response(
            200, json={"display_phone_number": "+1 555 000 1111", "verified_name": "Nile"}
        )
    )
    payload = {
        "phone_number_id": PHONE_NUMBER_ID,
        "waba_id": "WABA_ID_1",
        "display_phone": "",
        "system_token": SYSTEM_TOKEN,
        "app_secret": APP_SECRET,
        "verify_token": VERIFY_TOKEN,
    }
    with TestClient(wa_app) as client:
        owner, manager, viewer = (
            auth_headers(OWNER_ID),
            auth_headers(AGENCY_ID),
            auth_headers(STRANGER_ID),
        )
        # Nothing yet ⇒ 404 for OWNER/MANAGER, 403 for VIEWER.
        assert client.get(f"{BASE}/account", headers=owner).status_code == 404
        assert client.get(f"{BASE}/account", headers=viewer).status_code == 403
        # Create: MANAGER/VIEWER 403, OWNER 201.
        assert client.post(f"{BASE}/account", json=payload, headers=manager).status_code == 403
        assert client.post(f"{BASE}/account", json=payload, headers=viewer).status_code == 403
        created = client.post(f"{BASE}/account", json=payload, headers=owner)
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["phone_number_id"] == PHONE_NUMBER_ID and body["status"] == "active"
        assert body["display_phone"] == "+1 555 000 1111"
        for forbidden in (SYSTEM_TOKEN, APP_SECRET, VERIFY_TOKEN, "encrypted", "hash", "token"):
            assert (
                forbidden not in created.text.lower()
                if forbidden.islower()
                else forbidden not in created.text
            )
        # Duplicate ⇒ 409.
        assert client.post(f"{BASE}/account", json=payload, headers=owner).status_code == 409
        # Read: OWNER/MANAGER 200, VIEWER 403, stranger-brand 404.
        assert client.get(f"{BASE}/account", headers=manager).status_code == 200
        assert client.get(f"{BASE}/account", headers=viewer).status_code == 403
        other = client.get(f"{API}/brands/{uuid4()}/whatsapp/account", headers=owner)
        assert other.status_code == 404 and other.json()["detail"]["error"] == "brand_not_found"
        # Delete: MANAGER 403, OWNER ⇒ disabled.
        assert client.delete(f"{BASE}/account", headers=manager).status_code == 403
        disabled = client.delete(f"{BASE}/account", headers=owner)
        assert disabled.status_code == 200 and disabled.json()["status"] == "disabled"
    (account,) = wa_repo.accounts.values()
    assert account.status is AccountStatus.DISABLED


def test_b2_u1_connect_with_bad_token_is_422_no_row(
    wa_app: FastAPI, wa_repo: FakeWhatsAppRepository, auth_headers, graph_mock: respx.MockRouter
) -> None:
    graph_mock.get(f"/{PHONE_NUMBER_ID}").mock(return_value=_graph_error(190, 401))
    payload = {
        "phone_number_id": PHONE_NUMBER_ID,
        "waba_id": "WABA_ID_1",
        "system_token": SYSTEM_TOKEN,
        "app_secret": APP_SECRET,
        "verify_token": VERIFY_TOKEN,
    }
    with TestClient(wa_app) as client:
        res = client.post(f"{BASE}/account", json=payload, headers=auth_headers(OWNER_ID))
    assert res.status_code == 422
    assert res.json()["detail"]["error"] == "whatsapp_credentials_invalid"
    assert "leak?" not in res.text
    assert wa_repo.accounts == {}


# ---------------------------------------------------------------------- B2-U2 ---
async def test_b2_u2_inbox_listing_visibility_masking_and_messages_view(
    wa_app: FastAPI, wa_repo: FakeWhatsAppRepository, auth_headers, roles: None, wa_account
) -> None:
    conv = await _seed_conversation(wa_repo, wa_account.id, body="أ" * 200)
    with TestClient(wa_app) as client:
        owner, viewer = auth_headers(OWNER_ID), auth_headers(STRANGER_ID)
        listed = client.get(f"{BASE}/conversations", headers=owner)
        assert listed.status_code == 200, listed.text
        (item,) = listed.json()["items"]
        assert item["id"] == str(conv.id) and item["wa_id"] == CUSTOMER_WA_ID
        assert item["profile_name"] == "Nour" and item["unread_count"] == 1
        assert item["window_open"] is True and item["pending_draft"] is False
        assert len(item["last_message_preview"]) == 80
        assert item["last_message_preview"].endswith("…")
        assert listed.json()["next_cursor"] is None

        masked = client.get(f"{BASE}/conversations", headers=viewer)
        assert masked.status_code == 200
        wa_id = masked.json()["items"][0]["wa_id"]
        assert MASKED_WA_ID.match(wa_id) and wa_id == "...7890"
        assert CUSTOMER_WA_ID not in masked.text

        archived = client.get(f"{BASE}/conversations?status=archived", headers=owner)
        assert archived.json()["items"] == []
        everything = client.get(f"{BASE}/conversations?status=", headers=owner)
        assert everything.status_code == 422  # empty enum value is rejected

        messages = client.get(f"{BASE}/conversations/{conv.id}/messages", headers=viewer)
        assert messages.status_code == 200, messages.text
        page = messages.json()
        assert page["conversation"]["id"] == str(conv.id)
        assert [m["direction"] for m in page["items"]] == ["inbound"]
        assert page["items"][0]["body"].startswith("أ")  # body visible to VIEWER
        assert page["reply_drafts"] == [] and page["next_cursor"] is None

        missing = client.get(f"{BASE}/conversations/{uuid4()}/messages", headers=owner)
        assert missing.status_code == 404
        assert missing.json()["detail"]["error"] == "whatsapp_conversation_not_found"
        bad_cursor = client.get(f"{BASE}/conversations?cursor=%%%", headers=owner)
        assert bad_cursor.status_code == 422
        assert bad_cursor.json()["detail"]["error"] == "invalid_cursor"
        # VIEWER may not read-mark, archive or send.
        assert (
            client.post(f"{BASE}/conversations/{conv.id}/read", headers=viewer).status_code == 403
        )
        assert (
            client.post(f"{BASE}/conversations/{conv.id}/archive", headers=viewer).status_code
            == 403
        )
        send = client.post(
            f"{BASE}/conversations/{conv.id}/messages", json={"body": "x"}, headers=viewer
        )
        assert send.status_code == 403


async def test_b2_u2_read_and_archive(
    wa_app: FastAPI,
    wa_repo: FakeWhatsAppRepository,
    auth_headers,
    roles: None,
    wa_account,
    graph_mock: respx.MockRouter,
) -> None:
    conv = await _seed_conversation(wa_repo, wa_account.id)
    # The read receipt to Meta fails ⇒ the request still succeeds (local state wins).
    read_route = graph_mock.post(MESSAGES).mock(return_value=httpx.Response(503, json={}))
    with TestClient(wa_app) as client:
        manager = auth_headers(AGENCY_ID)
        res = client.post(f"{BASE}/conversations/{conv.id}/read", headers=manager)
        assert res.status_code == 200, res.text
        assert res.json()["unread_count"] == 0
        assert len(read_route.calls) == 1
        assert json.loads(read_route.calls[0].request.content)["status"] == "read"
        arch = client.post(f"{BASE}/conversations/{conv.id}/archive", headers=manager)
        assert arch.status_code == 200 and arch.json()["status"] == "archived"
        assert client.get(f"{BASE}/conversations", headers=manager).json()["items"] == []
        archived = client.get(f"{BASE}/conversations?status=archived", headers=manager)
        assert [c["id"] for c in archived.json()["items"]] == [str(conv.id)]
    assert wa_repo.conversations[conv.id].status is ConversationStatus.ARCHIVED


# ---------------------------------------------------------------------- B2-U3 ---
async def test_b2_u3_send_text_inside_window_single_post_with_human_actor(
    wa_app: FastAPI,
    wa_repo: FakeWhatsAppRepository,
    auth_headers,
    roles: None,
    wa_account,
    graph_mock: respx.MockRouter,
) -> None:
    counters.reset()
    conv = await _seed_conversation(wa_repo, wa_account.id)
    route = graph_mock.post(MESSAGES).mock(return_value=_ok("wamid.SENT1"))
    with TestClient(wa_app) as client:
        res = client.post(
            f"{BASE}/conversations/{conv.id}/messages",
            json={"body": "أهلاً بك، الفرع مفتوح حتى العاشرة."},
            headers=auth_headers(AGENCY_ID),
        )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["delivery_status"] == "sent" and body["duplicate"] is False
    assert body["message"]["direction"] == "outbound"
    assert body["message"]["sent_by_user_id"] == str(USER_BY_TENANT[AGENCY_ID])  # §2-0 أ
    assert len(route.calls) == 1
    request = route.calls[0].request
    assert request.headers["Authorization"] == f"Bearer {SYSTEM_TOKEN}"
    assert json.loads(request.content)["to"] == CUSTOMER_WA_ID
    stored = wa_repo.messages[UUID(body["message"]["id"])]
    assert stored.delivery_status is DeliveryStatus.SENT and stored.wamid == "wamid.SENT1"
    assert stored.sent_by_user_id == USER_BY_TENANT[AGENCY_ID]
    assert wa_repo.conversations[conv.id].last_outbound_at is not None
    assert counters["wa_sent"] == 1


async def test_b2_u3_send_text_outside_window_is_422_template_required_no_call(
    wa_app: FastAPI,
    wa_repo: FakeWhatsAppRepository,
    auth_headers,
    roles: None,
    wa_account,
    graph_mock: respx.MockRouter,
) -> None:
    conv = await _seed_conversation(wa_repo, wa_account.id, inbound_ago=timedelta(hours=25))
    route = graph_mock.post(MESSAGES).mock(return_value=_ok())
    with TestClient(wa_app) as client:
        res = client.post(
            f"{BASE}/conversations/{conv.id}/messages",
            json={"body": "late"},
            headers=auth_headers(OWNER_ID),
        )
        assert res.status_code == 422, res.text
        assert res.json()["detail"] == {
            "error": "whatsapp_window_closed",
            "template_required": True,
        }
        assert len(route.calls) == 0
        assert all(m.direction is Direction.INBOUND for m in wa_repo.messages.values())
        # Templates still work outside the window.
        tpl = client.post(
            f"{BASE}/conversations/{conv.id}/template",
            json={"name": "order_update", "language_code": "ar"},
            headers=auth_headers(OWNER_ID),
        )
        assert tpl.status_code == 201, tpl.text
        assert tpl.json()["message"]["kind"] == "template"
        assert tpl.json()["message"]["template_name"] == "order_update"
        assert len(route.calls) == 1
        assert json.loads(route.calls[0].request.content)["type"] == "template"


@pytest.mark.parametrize(
    ("response", "http_status", "error", "delivery"),
    [
        (httpx.Response(502, json={}), 202, None, "ambiguous"),
        (httpx.ReadTimeout("slow"), 202, None, "ambiguous"),
        (httpx.ConnectError("refused"), 503, "whatsapp_transient", "failed"),
        (_graph_error(130429), 429, "whatsapp_rate_limited", "failed"),
        (_graph_error(131047), 422, "whatsapp_recipient_rejected", "failed"),
        (_graph_error(100), 422, "whatsapp_send_failed", "failed"),
    ],
    ids=["5xx", "read-timeout", "connect", "rate-limit", "recipient", "permanent"],
)
async def test_b2_u3_send_failure_mapping_single_call(
    wa_app: FastAPI,
    wa_repo: FakeWhatsAppRepository,
    auth_headers,
    roles: None,
    wa_account,
    graph_mock: respx.MockRouter,
    response,
    http_status: int,
    error: str | None,
    delivery: str,
) -> None:
    counters.reset()
    conv = await _seed_conversation(wa_repo, wa_account.id)
    route = graph_mock.post(MESSAGES).mock(side_effect=[response, _ok()])
    with TestClient(wa_app) as client:
        res = client.post(
            f"{BASE}/conversations/{conv.id}/messages",
            json={"body": "hi"},
            headers=auth_headers(OWNER_ID),
        )
    assert res.status_code == http_status, res.text
    assert len(route.calls) == 1  # D-070: never a second send
    outbound = [m for m in wa_repo.messages.values() if m.direction is Direction.OUTBOUND]
    assert len(outbound) == 1 and outbound[0].delivery_status.value == delivery
    assert "leak?" not in res.text
    if error is None:
        assert res.json()["delivery_status"] == "ambiguous"
        assert counters["wa_send_ambiguous"] == 1
    else:
        assert res.json()["detail"]["error"] == error
        assert counters["wa_send_failed"] == 1
    if http_status == 503:
        assert res.headers["Retry-After"] == "30"
    if error == "whatsapp_recipient_rejected":
        assert res.json()["detail"]["code"] == 131047


async def test_b2_u3_auth_error_disables_account_and_409_reauth(
    wa_app: FastAPI,
    wa_repo: FakeWhatsAppRepository,
    auth_headers,
    roles: None,
    wa_account,
    graph_mock: respx.MockRouter,
) -> None:
    conv = await _seed_conversation(wa_repo, wa_account.id)
    graph_mock.post(MESSAGES).mock(return_value=_graph_error(190, 401))
    with TestClient(wa_app) as client:
        res = client.post(
            f"{BASE}/conversations/{conv.id}/messages",
            json={"body": "hi"},
            headers=auth_headers(OWNER_ID),
        )
        assert res.status_code == 409, res.text
        assert res.json()["detail"] == {
            "error": "whatsapp_reauth_required",
            "required_action": "reauth",
        }
        assert wa_repo.accounts[wa_account.id].status is AccountStatus.DISABLED
        # A disabled account cannot send anything any more.
        again = client.post(
            f"{BASE}/conversations/{conv.id}/messages",
            json={"body": "hi"},
            headers=auth_headers(OWNER_ID),
        )
        # SPRINT-10 A-1: a disabled account answers 409 reauth_required, never calls Graph.
        assert again.status_code == 409
        assert again.json()["detail"]["error"] == "whatsapp_reauth_required"


# ---------------------------------------------------------------------- B2-U4 ---
async def test_b2_u4_body_limits_and_template_validation(
    wa_app: FastAPI, wa_repo: FakeWhatsAppRepository, auth_headers, roles: None, wa_account
) -> None:
    conv = await _seed_conversation(wa_repo, wa_account.id)
    with TestClient(wa_app) as client:
        owner = auth_headers(OWNER_ID)
        too_long = client.post(
            f"{BASE}/conversations/{conv.id}/messages", json={"body": "x" * 4097}, headers=owner
        )
        assert too_long.status_code == 422
        empty = client.post(
            f"{BASE}/conversations/{conv.id}/messages", json={"body": ""}, headers=owner
        )
        assert empty.status_code == 422
        extra = client.post(
            f"{BASE}/conversations/{conv.id}/messages",
            json={"body": "x", "auto": True},
            headers=owner,
        )
        assert extra.status_code == 422
        bad_tpl = client.post(
            f"{BASE}/conversations/{conv.id}/template",
            json={"name": "Order Update", "language_code": "ar"},
            headers=owner,
        )
        assert bad_tpl.status_code == 422
        unknown_conv = client.post(
            f"{BASE}/conversations/{uuid4()}/messages", json={"body": "x"}, headers=owner
        )
        assert unknown_conv.status_code == 404
    assert all(m.direction is Direction.INBOUND for m in wa_repo.messages.values())


# ---------------------------------------------------------------------- B2-U5 ---
async def test_b2_u5_idempotency_key_replays_without_second_send(
    wa_app: FastAPI,
    wa_repo: FakeWhatsAppRepository,
    auth_headers,
    roles: None,
    wa_account,
    graph_mock: respx.MockRouter,
) -> None:
    conv = await _seed_conversation(wa_repo, wa_account.id)
    route = graph_mock.post(MESSAGES).mock(return_value=_ok("wamid.IDEM"))
    key = str(uuid4())
    with TestClient(wa_app) as client:
        headers = {**auth_headers(OWNER_ID), "Idempotency-Key": key}
        first = client.post(
            f"{BASE}/conversations/{conv.id}/messages", json={"body": "once"}, headers=headers
        )
        assert first.status_code == 201, first.text
        replay = client.post(
            f"{BASE}/conversations/{conv.id}/messages", json={"body": "once"}, headers=headers
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["duplicate"] is True
        assert replay.json()["message"]["id"] == first.json()["message"]["id"]
        assert len(route.calls) == 1
        # Template path honours the same key space.
        tpl_headers = {**auth_headers(OWNER_ID), "Idempotency-Key": str(uuid4())}
        t1 = client.post(
            f"{BASE}/conversations/{conv.id}/template",
            json={"name": "hello", "language_code": "en"},
            headers=tpl_headers,
        )
        t2 = client.post(
            f"{BASE}/conversations/{conv.id}/template",
            json={"name": "hello", "language_code": "en"},
            headers=tpl_headers,
        )
        assert (t1.status_code, t2.status_code) == (201, 200)
        assert len(route.calls) == 2
        bad_key = client.post(
            f"{BASE}/conversations/{conv.id}/messages",
            json={"body": "x"},
            headers={**auth_headers(OWNER_ID), "Idempotency-Key": "not-a-uuid"},
        )
        assert bad_key.status_code == 422
    outbound = [m for m in wa_repo.messages.values() if m.direction is Direction.OUTBOUND]
    assert len(outbound) == 2
    assert {str(m.client_message_id) for m in outbound} == {key, tpl_headers["Idempotency-Key"]}
