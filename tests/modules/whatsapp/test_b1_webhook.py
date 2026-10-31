"""SPRINT-09 B-1 — signed webhook (B1-U1..U5) against the fake repository."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.core.metrics import counters
from src.modules.whatsapp.domain.models import (
    AccountStatus,
    ConversationStatus,
    DeliveryStatus,
    Direction,
    Message,
    MessageKind,
)
from src.modules.whatsapp.webhook import compute_signature, extract_phone_number_ids

from tests.conftest import BRAND2_ID, BRAND_ID, OWNER_ID, USER_BY_TENANT
from tests.fakes import FakeWhatsAppRepository
from tests.modules.whatsapp.conftest import (
    APP_SECRET,
    APP_SECRET_2,
    CUSTOMER_WA_ID,
    PHONE_NUMBER_ID,
    PHONE_NUMBER_ID_2,
    VERIFY_TOKEN,
    load_payload,
)

WEBHOOK = "/api/v1/webhooks/whatsapp"


def _post(client: TestClient, payload: dict, *, secret: str | None = APP_SECRET, header=None):
    raw = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if header is not None:
        headers["X-Hub-Signature-256"] = header
    elif secret is not None:
        headers["X-Hub-Signature-256"] = compute_signature(secret, raw)
    return client.post(WEBHOOK, content=raw, headers=headers)


def _handshake(client: TestClient, **params: str):
    return client.get(WEBHOOK, params=params)


# ---------------------------------------------------------------------- B1-U1 ---
def test_b1_u1_post_without_or_with_bad_signature_is_401_and_nothing_is_stored(
    wa_app: FastAPI, wa_repo: FakeWhatsAppRepository, wa_account
) -> None:
    counters.reset()
    payload = load_payload("inbound_text")
    with TestClient(wa_app) as client:
        missing = _post(client, payload, secret=None)
        assert missing.status_code == 401
        assert missing.json()["detail"]["error"] == "webhook_unverified"
        assert _post(client, payload, secret="not-the-app-secret").status_code == 401
        assert _post(client, payload, header="md5=abc").status_code == 401
        # Unknown phone_number_id ⇒ 401 even with a signature for some other secret.
        other = json.loads(json.dumps(payload))
        other["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"] = "PNID_UNKNOWN"
        assert _post(client, other).status_code == 401
    assert wa_repo.messages == {} and wa_repo.conversations == {}
    assert counters["wa_webhook_rejected"] == 4
    assert counters["wa_inbound"] == 0


def test_b1_u1_get_handshake_returns_challenge_only_for_matching_verify_token(
    wa_app: FastAPI, wa_account
) -> None:
    with TestClient(wa_app) as client:
        ok = _handshake(
            client,
            **{
                "hub.mode": "subscribe",
                "hub.verify_token": VERIFY_TOKEN,
                "hub.challenge": "12345",
            },
        )
        assert ok.status_code == 200
        assert ok.text == "12345"
        assert ok.headers["content-type"].startswith("text/plain")
        bad = _handshake(
            client, **{"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"}
        )
        assert bad.status_code == 403
        wrong_mode = _handshake(
            client,
            **{"hub.mode": "unsubscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "1"},
        )
        assert wrong_mode.status_code == 403
        assert _handshake(client).status_code == 403


# ---------------------------------------------------------------------- B1-U2 ---
def test_b1_u2_inbound_text_creates_conversation_and_message_then_duplicate_is_noop(
    wa_app: FastAPI, wa_repo: FakeWhatsAppRepository, wa_account
) -> None:
    counters.reset()
    payload = load_payload("inbound_text")
    with TestClient(wa_app) as client:
        first = _post(client, payload)
        assert first.status_code == 200, first.text
        assert first.json() == {
            "inbound": 1,
            "duplicates": 0,
            "status_updates": 0,
            "ignored_status_updates": 0,  # SPRINT-10 A-3
            "unparsed": 0,
            "expired_drafts": [],
        }
        again = _post(client, payload)  # Meta redelivery of the same wamid
        assert again.status_code == 200
        assert again.json() == {
            "inbound": 0,
            "duplicates": 1,
            "status_updates": 0,
            "ignored_status_updates": 0,  # SPRINT-10 A-3
            "unparsed": 0,
            "expired_drafts": [],
        }

    assert len(wa_repo.messages) == 1
    (message,) = wa_repo.messages.values()
    assert message.direction is Direction.INBOUND and message.kind is MessageKind.TEXT
    assert message.body == "مرحبا، هل الفرع مفتوح اليوم؟"
    assert message.delivery_status is DeliveryStatus.RECEIVED
    assert message.wamid.startswith("wamid.")
    assert message.brand_id == BRAND_ID
    assert message.platform_timestamp == datetime.fromtimestamp(1761900000, tz=UTC)
    (conv,) = wa_repo.conversations.values()
    assert conv.wa_id == CUSTOMER_WA_ID and conv.profile_name == "Nour Customer"
    assert conv.unread_count == 1  # the duplicate did not bump it twice
    assert conv.status is ConversationStatus.OPEN
    assert conv.window_expires_at - conv.last_inbound_at == timedelta(hours=24)
    assert counters["wa_inbound"] == 1 and counters["wa_duplicate"] == 1


async def test_b1_u2_inbound_on_archived_conversation_reopens_it(
    wa_app: FastAPI, wa_repo: FakeWhatsAppRepository, wa_account
) -> None:
    payload = load_payload("inbound_text")
    with TestClient(wa_app) as client:
        assert _post(client, payload).status_code == 200
        (conv,) = wa_repo.conversations.values()
        await wa_repo.set_conversation_status(
            conv.id, ConversationStatus.ARCHIVED, now=datetime.now(tz=UTC)
        )
        second = json.loads(json.dumps(payload))
        msg = second["entry"][0]["changes"][0]["value"]["messages"][0]
        msg["id"], msg["timestamp"] = "wamid.SECOND", "1761900500"
        assert _post(client, second).status_code == 200
    (conv,) = wa_repo.conversations.values()
    assert conv.status is ConversationStatus.OPEN and conv.unread_count == 2
    assert conv.last_inbound_at == datetime.fromtimestamp(1761900500, tz=UTC)


# ---------------------------------------------------------------------- B1-U3 ---
async def test_b1_u3_statuses_update_outbound_delivery_status_and_error_code(
    wa_app: FastAPI, wa_repo: FakeWhatsAppRepository, wa_account
) -> None:
    counters.reset()
    now = datetime.now(tz=UTC)
    conv = await wa_repo.upsert_conversation_inbound(
        brand_id=BRAND_ID,
        account_id=wa_account.id,
        wa_id=CUSTOMER_WA_ID,
        profile_name=None,
        at=now,
        window_expires_at=now,
    )
    outbound = await wa_repo.insert_outbound(
        Message(
            conversation_id=conv.id,
            brand_id=BRAND_ID,
            direction=Direction.OUTBOUND,
            wamid="wamid.OUT0001",
            kind=MessageKind.TEXT,
            body="hello",
            delivery_status=DeliveryStatus.SENT,
            sent_by_user_id=USER_BY_TENANT[OWNER_ID],
            created_at=now,
        )
    )
    with TestClient(wa_app) as client:
        # SPRINT-10 A-3 ranks: ``sent ⇒ failed`` is **not** allowed any more (failed only from
        # queued/ambiguous) ⇒ ignored; then ``sent ⇒ delivered`` moves forward.
        res = _post(client, load_payload("status_failed"))
        assert res.status_code == 200, res.text
        assert res.json()["status_updates"] == 0 and res.json()["ignored_status_updates"] == 1
        assert wa_repo.messages[outbound.id].delivery_status is DeliveryStatus.SENT
        res = _post(client, load_payload("status_delivered"))
        assert res.status_code == 200 and res.json()["status_updates"] == 1
        assert wa_repo.messages[outbound.id].delivery_status is DeliveryStatus.DELIVERED
        # A queued row takes ``failed`` with the error code.
        queued = await wa_repo.insert_outbound(
            outbound.model_copy(
                update={
                    "id": uuid4(),
                    "wamid": "wamid.OUT0002",
                    "delivery_status": DeliveryStatus.QUEUED,
                }
            )
        )
        failed_payload = load_payload("status_failed")
        failed_payload["entry"][0]["changes"][0]["value"]["statuses"][0]["id"] = "wamid.OUT0002"
        res = _post(client, failed_payload)
        assert res.status_code == 200 and res.json()["status_updates"] == 1
        failed = wa_repo.messages[queued.id]
        assert failed.delivery_status is DeliveryStatus.FAILED and failed.error_code == 131047
        # Status for a wamid we never sent: verified ⇒ still 200, nothing updated (ignored).
        unknown = load_payload("status_delivered")
        unknown["entry"][0]["changes"][0]["value"]["statuses"][0]["id"] = "wamid.NOBODY"
        res = _post(client, unknown)
        assert res.status_code == 200 and res.json()["status_updates"] == 0
        assert res.json()["ignored_status_updates"] == 1
        # Unknown status label ⇒ unparsed, still 200.
        weird = load_payload("status_delivered")
        weird["entry"][0]["changes"][0]["value"]["statuses"][0]["status"] = "teleported"
        res = _post(client, weird)
        assert res.status_code == 200 and res.json()["unparsed"] == 1
    assert counters["wa_status_updates"] == 2
    assert counters["wa_status_ignored"] == 2  # A-3


# ---------------------------------------------------------------------- B1-U4 ---
def test_b1_u4_unknown_type_is_stored_as_unsupported_and_media_carries_media_id(
    wa_app: FastAPI, wa_repo: FakeWhatsAppRepository, wa_account
) -> None:
    counters.reset()
    with TestClient(wa_app) as client:
        assert _post(client, load_payload("inbound_unsupported")).status_code == 200
        assert _post(client, load_payload("inbound_image")).status_code == 200
        # Verified but structurally useless payloads are still 200 (Meta must not retry).
        junk = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "metadata": {"phone_number_id": PHONE_NUMBER_ID},
                                "messages": ["not-a-dict"],
                                "statuses": [7],
                            }
                        },
                        "not-a-change",
                    ]
                },
                "not-an-entry",
            ],
        }
        res = _post(client, junk)
        assert res.status_code == 200 and res.json()["unparsed"] == 4
        wrong_object = {
            "object": "page",
            "entry": [
                {"changes": [{"value": {"metadata": {"phone_number_id": PHONE_NUMBER_ID}}}]}
            ],
        }
        res = _post(client, wrong_object)
        assert res.status_code == 200 and res.json()["unparsed"] == 1
    kinds = {m.wamid: m for m in wa_repo.messages.values()}
    assert kinds["wamid.REACTION0001"].kind is MessageKind.UNSUPPORTED
    assert kinds["wamid.REACTION0001"].body is None
    image = kinds["wamid.IMAGE0001"]
    assert image.kind is MessageKind.IMAGE and image.media_id == "MEDIA_ID_42"
    assert image.body == "صورة المنتج"  # caption
    assert counters["wa_webhook_unparsed"] == 5 and counters["wa_inbound"] == 2


# ---------------------------------------------------------------------- B1-U5 ---
def test_b1_u5_brand_isolation_signature_is_checked_against_the_payloads_own_account(
    wa_app: FastAPI, wa_repo: FakeWhatsAppRepository, wa_account, wa_account_brand2
) -> None:
    """A payload for brand 2's number signed with brand 1's app secret is rejected; signed
    with brand 2's secret it lands in brand 2 only."""
    payload = load_payload("inbound_text")
    payload["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"] = PHONE_NUMBER_ID_2
    with TestClient(wa_app) as client:
        cross = _post(client, payload, secret=APP_SECRET)  # brand 1's secret
        assert cross.status_code == 401
        ok = _post(client, payload, secret=APP_SECRET_2)
        assert ok.status_code == 200 and ok.json()["inbound"] == 1
    (conv,) = wa_repo.conversations.values()
    assert conv.brand_id == BRAND2_ID and conv.account_id == wa_account_brand2.id
    assert all(m.brand_id == BRAND2_ID for m in wa_repo.messages.values())


def test_b1_u5_extract_phone_number_ids_tolerates_garbage() -> None:
    assert extract_phone_number_ids(None) == []
    assert extract_phone_number_ids({"entry": "x"}) == []
    assert extract_phone_number_ids({"entry": [1, {"changes": [2, {"value": 3}]}]}) == []
    two = {
        "entry": [
            {"changes": [{"value": {"metadata": {"phone_number_id": "A"}}}]},
            {
                "changes": [
                    {"value": {"metadata": {"phone_number_id": "B"}}},
                    {"value": {"metadata": {"phone_number_id": "A"}}},
                ]
            },
        ]
    }
    assert extract_phone_number_ids(two) == ["A", "B"]


def test_b1_webhook_rejects_non_json_body_as_unverified(wa_app: FastAPI, wa_account) -> None:
    with TestClient(wa_app) as client:
        res = client.post(
            WEBHOOK, content=b"\xff\xfe not json", headers={"X-Hub-Signature-256": "sha256=00"}
        )
    assert res.status_code == 401


def test_b1_webhook_disabled_account_is_unknown(
    wa_app: FastAPI, wa_repo: FakeWhatsAppRepository, wa_account
) -> None:
    asyncio.run(
        wa_repo.set_account_status(wa_account.id, AccountStatus.DISABLED, now=datetime.now(tz=UTC))
    )
    with TestClient(wa_app) as client:
        assert _post(client, load_payload("inbound_text")).status_code == 401
        res = _handshake(
            client,
            **{"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "1"},
        )
        assert res.status_code == 403


@pytest.mark.parametrize("field", ["id", "from"])
def test_b1_webhook_message_without_id_or_from_counts_as_unparsed(
    wa_app: FastAPI, wa_repo: FakeWhatsAppRepository, wa_account, field: str
) -> None:
    payload = load_payload("inbound_text")
    del payload["entry"][0]["changes"][0]["value"]["messages"][0][field]
    with TestClient(wa_app) as client:
        res = _post(client, payload)
    assert res.status_code == 200 and res.json()["unparsed"] == 1
    assert wa_repo.messages == {}


def test_b1_webhook_bad_timestamp_falls_back_to_now(
    wa_app: FastAPI, wa_repo: FakeWhatsAppRepository, wa_account
) -> None:
    payload = load_payload("inbound_text")
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["timestamp"] = "not-a-number"
    before = datetime.now(tz=UTC)
    with TestClient(wa_app) as client:
        assert _post(client, payload).status_code == 200
    (message,) = wa_repo.messages.values()
    assert message.platform_timestamp >= before
