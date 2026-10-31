"""SPRINT-10 Part A — unit tests (fake repository). The D-072 *persistence* proofs
(A1-I1/I2/I3, A2-I1/I2, A3-I1/I2, A4-I1/I2, A5-I1/M1) live in
``tests/integration/test_postgres_persistence.py`` on PostgreSQL."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.core.metrics import counters
from src.core.security import TokenEncryptionService
from src.modules.ai_gateway.budget import DailyBudget, NullBudgetUsageReader
from src.modules.tenancy.domain.models import GrantRole
from src.modules.whatsapp import webhook as webhook_module
from src.modules.whatsapp.client import WhatsAppCloudClient
from src.modules.whatsapp.domain.models import (
    DELIVERY_RANK,
    DeliveryStatus,
    Direction,
    MessageKind,
    delivery_transition_allowed,
)
from src.modules.whatsapp.exceptions import CredentialsInvalidError, PhoneNumberInUseError
from src.modules.whatsapp.service import WhatsAppAccountService
from src.modules.whatsapp.suggestions import resolve_language
from src.modules.whatsapp.webhook import MAX_BODY_BYTES, WEBHOOK_PATH, compute_signature

from tests.conftest import AGENCY_ID, BRAND_ID, OWNER_ID, STRANGER_ID, USER_BY_TENANT, grant
from tests.fakes import FakeStore, FakeWhatsAppRepository
from tests.modules.whatsapp.conftest import (
    APP_SECRET,
    PHONE_NUMBER_ID,
    SYSTEM_TOKEN,
    VERIFY_TOKEN,
    load_payload,
)
from tests.modules.whatsapp.test_b2_inbox import _seed_conversation
from tests.modules.whatsapp.test_b3_suggestions import _suggest

API = "/api/v1"
BASE = f"{API}/brands/{BRAND_ID}/whatsapp"
WEBHOOK = f"{API}{WEBHOOK_PATH}"


@pytest.fixture
def roles(store: FakeStore) -> None:
    grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    grant(store, BRAND_ID, STRANGER_ID, GrantRole.VIEWER)


# ------------------------------------------------------------------------ A2-U1 ---
async def test_a2_u1_rotate_with_rejected_token_is_422_and_changes_nothing(
    wa_client: WhatsAppCloudClient,
    graph_mock: respx.MockRouter,
    encryption: TokenEncryptionService,
    wa_repo: FakeWhatsAppRepository,
    wa_account,
) -> None:
    graph_mock.get(f"/{PHONE_NUMBER_ID}").mock(
        return_value=httpx.Response(
            401, json={"error": {"message": "x", "code": 190, "fbtrace_id": "t"}}
        )
    )
    service = WhatsAppAccountService(wa_repo, wa_client, encryption)
    before = wa_repo.accounts[wa_account.id]
    with pytest.raises(CredentialsInvalidError):
        await service.rotate_credentials(
            BRAND_ID, system_token="EAAnewTokenThatMetaRejects", app_secret="new-secret"
        )
    assert wa_repo.accounts[wa_account.id] == before


async def test_a2_u1_rotate_with_accepted_token_replaces_secrets_only(
    wa_client: WhatsAppCloudClient,
    graph_mock: respx.MockRouter,
    encryption: TokenEncryptionService,
    wa_repo: FakeWhatsAppRepository,
    wa_account,
) -> None:
    graph_mock.get(f"/{PHONE_NUMBER_ID}").mock(
        return_value=httpx.Response(200, json={"display_phone_number": "+1 555 000 1111"})
    )
    service = WhatsAppAccountService(wa_repo, wa_client, encryption)
    updated = await service.rotate_credentials(
        BRAND_ID, system_token="EAAnewTokenAcceptedByMeta", app_secret="new-secret"
    )
    stored = wa_repo.accounts[wa_account.id]
    assert updated.id == wa_account.id and stored.phone_number_id == PHONE_NUMBER_ID
    assert encryption.decrypt_token(stored.encrypted_system_token) == "EAAnewTokenAcceptedByMeta"
    assert encryption.decrypt_token(stored.encrypted_app_secret) == "new-secret"
    assert stored.verify_token_hash == wa_account.verify_token_hash  # untouched
    assert stored.status.value == "active"


async def test_a2_reconnect_disabled_row_in_place_and_phone_number_clash(
    wa_client: WhatsAppCloudClient,
    graph_mock: respx.MockRouter,
    encryption: TokenEncryptionService,
    wa_repo: FakeWhatsAppRepository,
    wa_account,
    wa_account_brand2,
) -> None:
    graph_mock.get(f"/{PHONE_NUMBER_ID}").mock(
        return_value=httpx.Response(200, json={"display_phone_number": "+1 555 000 1111"})
    )
    service = WhatsAppAccountService(wa_repo, wa_client, encryption)
    await service.disable_account(BRAND_ID)
    outcome = await service.connect_account(
        BRAND_ID,
        USER_BY_TENANT[OWNER_ID],
        phone_number_id=PHONE_NUMBER_ID,
        waba_id="WABA_ID_1",
        display_phone="",
        system_token=SYSTEM_TOKEN,
        app_secret=APP_SECRET,
        verify_token=VERIFY_TOKEN,
    )
    assert outcome.reconnected is True and outcome.account.id == wa_account.id
    assert wa_repo.accounts[wa_account.id].status.value == "active"
    # Another brand's phone_number_id ⇒ 409 whatsapp_phone_number_in_use, no Graph call.
    await service.disable_account(BRAND_ID)
    calls_before = len(graph_mock.calls)
    with pytest.raises(PhoneNumberInUseError):
        await service.connect_account(
            BRAND_ID,
            USER_BY_TENANT[OWNER_ID],
            phone_number_id=wa_account_brand2.phone_number_id,
            waba_id="WABA_ID_1",
            display_phone="",
            system_token=SYSTEM_TOKEN,
            app_secret=APP_SECRET,
            verify_token=VERIFY_TOKEN,
        )
    assert len(graph_mock.calls) == calls_before


def test_a2_rotate_route_is_owner_only_and_absent_account_is_404(
    wa_app: FastAPI, auth_headers, roles: None, graph_mock: respx.MockRouter
) -> None:
    body = {"system_token": "EAAnewTokenAcceptedByMeta", "app_secret": "new-secret"}
    with TestClient(wa_app) as client:
        res = client.post(f"{BASE}/account/rotate", json=body, headers=auth_headers(AGENCY_ID))
        assert res.status_code == 403
        res = client.post(f"{BASE}/account/rotate", json=body, headers=auth_headers(OWNER_ID))
        assert res.status_code == 404
        assert res.json()["detail"]["error"] == "whatsapp_account_not_found"
    assert len(graph_mock.calls) == 0


# ------------------------------------------------------------------------ A3-U1 ---
_TRANSITIONS = {
    # current: set of accepted new statuses
    DeliveryStatus.QUEUED: {"sent", "delivered", "read", "failed", "ambiguous"},
    DeliveryStatus.SENT: {"delivered", "read"},
    DeliveryStatus.DELIVERED: {"read"},
    DeliveryStatus.READ: set(),
    DeliveryStatus.FAILED: set(),  # final
    DeliveryStatus.AMBIGUOUS: {"sent", "delivered", "read", "failed", "ambiguous"},
    DeliveryStatus.RECEIVED: set(),  # inbound rows never move
}


@pytest.mark.parametrize("current", list(DeliveryStatus))
@pytest.mark.parametrize("new", list(DeliveryStatus))
def test_a3_u1_delivery_rank_matrix(current: DeliveryStatus, new: DeliveryStatus) -> None:
    expected = new.value in _TRANSITIONS[current]
    assert delivery_transition_allowed(current, new) is expected
    assert list(DELIVERY_RANK.values()) == [1, 2, 3, 4]


# ------------------------------------------------------------------------ A5-U1 ---
def test_a5_u1_body_over_one_mib_is_413_with_zero_repository_calls(
    wa_app: FastAPI, wa_repo: FakeWhatsAppRepository, wa_account, monkeypatch
) -> None:
    counters.reset()
    calls = {"n": 0}
    original = wa_repo.get_account_by_phone_number_id

    async def _counting(pid: str):  # noqa: ANN202
        calls["n"] += 1
        return await original(pid)

    monkeypatch.setattr(wa_repo, "get_account_by_phone_number_id", _counting)
    raw = b"{" + b" " * MAX_BODY_BYTES + b"}"  # 1 MiB + 2 bytes
    with TestClient(wa_app) as client:
        res = client.post(
            WEBHOOK,
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": compute_signature(APP_SECRET, raw),
            },
        )
    assert res.status_code == 413, res.text
    assert res.json()["detail"] == {"error": "payload_too_large"}
    assert calls["n"] == 0
    assert counters["wa_webhook_rejected"] == 1


# ------------------------------------------------------------------------ A5-U2 ---
@pytest.mark.parametrize(
    "header",
    [None, "garbage", "sha256=zz" + "0" * 62, "sha256=" + "a" * 63, "SHA256=" + "a" * 64],
    ids=["missing", "garbage", "non-hex", "short", "upper-prefix"],
)
def test_a5_u2_malformed_signature_header_is_401_before_json_loads(
    wa_app: FastAPI, wa_repo: FakeWhatsAppRepository, wa_account, monkeypatch, header
) -> None:
    counters.reset()
    raw = json.dumps(load_payload("inbound_text")).encode()  # before the spy is installed
    loads = {"n": 0}
    real_loads = json.loads

    def _counting(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        loads["n"] += 1
        return real_loads(*args, **kwargs)

    monkeypatch.setattr(webhook_module.json, "loads", _counting)
    headers = {"Content-Type": "application/json"}
    if header is not None:
        headers["X-Hub-Signature-256"] = header
    with TestClient(wa_app) as client:
        res = client.post(WEBHOOK, content=raw, headers=headers)
    assert res.status_code == 401
    assert loads["n"] == 0
    assert len(wa_repo.messages) == 0
    assert counters["wa_webhook_rejected"] == 1


# ------------------------------------------------------------------------ A6-U1 ---
async def test_a6_u1_wamid_is_private_viewer_never_sees_it(
    wa_app: FastAPI, wa_repo: FakeWhatsAppRepository, auth_headers, roles: None, wa_account
) -> None:
    conv = await _seed_conversation(wa_repo, wa_account.id)
    with TestClient(wa_app) as client:
        owner = client.get(
            f"{BASE}/conversations/{conv.id}/messages", headers=auth_headers(OWNER_ID)
        ).json()
        viewer = client.get(
            f"{BASE}/conversations/{conv.id}/messages", headers=auth_headers(STRANGER_ID)
        ).json()
    assert owner["items"][0]["wamid"].startswith("wamid.IN.")
    assert viewer["items"][0]["wamid"] is None
    assert viewer["items"][0]["body"] == owner["items"][0]["body"]


# ------------------------------------------------------------------------ A6-U2 ---
@pytest.mark.parametrize(
    ("requested", "inbound", "expected"),
    [
        ("auto", "Hello, is the branch open today?", "en"),
        ("auto", "مرحبا، هل الفرع مفتوح؟", "ar"),
        ("auto", "Hello مرحبا open مفتوح today اليوم", "ar"),  # mixed ⇒ ar
        ("auto", "12345 !!!", "ar"),  # other ⇒ ar
        ("auto", None, "ar"),
        ("en", "مرحبا", "en"),  # explicit wins
        ("ar", "Hello", "ar"),
    ],
)
def test_a6_u2_resolve_language(requested, inbound, expected) -> None:  # noqa: ANN001
    assert resolve_language(requested, inbound) == expected


async def test_a6_u2_auto_english_inbound_gives_english_prompt_and_private_language_detected(
    wa_app: FastAPI,
    wa_repo: FakeWhatsAppRepository,
    auth_headers,
    roles: None,
    wa_account,
    monkeypatch,
) -> None:
    from src.modules.ai_gateway.facades.fake import FakeProvider

    seen: dict[str, str] = {}
    original = FakeProvider.complete

    async def _spy(self, request):  # noqa: ANN001, ANN202
        seen["system"] = request.system
        return await original(self, request)

    monkeypatch.setattr(FakeProvider, "complete", _spy)
    conv = await _seed_conversation(wa_repo, wa_account.id, body="Hello, is the branch open?")
    with TestClient(wa_app) as client:
        res = _suggest(client, auth_headers(OWNER_ID), conv.id, language="auto")
        assert res.status_code == 201, res.text
        draft = res.json()
        assert draft["provenance"]["language"] == "auto"
        assert draft["provenance"]["language_detected"] == "en"
        thread = client.get(
            f"{BASE}/conversations/{conv.id}/messages", headers=auth_headers(STRANGER_ID)
        ).json()
    assert "Always answer in English" in seen["system"]
    assert "language_detected" not in thread["reply_drafts"][0]["provenance"]  # private


# ------------------------------------------------------------------------ A4 (unit) ---
async def test_a4_daily_budget_verdict_and_retry_after_from_shared_reader() -> None:
    class Reader:
        async def combined_usage_since(self, brand_id, since):  # noqa: ANN001, ANN202
            return 1_000

        async def oldest_generation_since(self, brand_id, since):  # noqa: ANN001, ANN202
            return datetime.now(tz=UTC)

    budget = DailyBudget(1_000)
    verdict = await budget.check(BRAND_ID, usage_reader=Reader())
    assert verdict.allowed is False and verdict.used == 1_000 and verdict.budget == 1_000
    assert 0 < verdict.resets_in_seconds <= 24 * 3600
    ok = await DailyBudget(1_001).check(BRAND_ID, usage_reader=Reader())
    assert ok.allowed is True and ok.resets_in_seconds == 0
    empty = await DailyBudget(1).check(BRAND_ID, usage_reader=NullBudgetUsageReader())
    assert empty.allowed is True and empty.used == 0


async def test_a4_wa_draft_tokens_block_content_generate_in_the_fake_world(
    wa_app: FastAPI, wa_repo: FakeWhatsAppRepository, auth_headers, roles: None, wa_account
) -> None:
    """Fake mirror of A4-I1: one WhatsApp draft with ``budget + 1`` tokens ⇒ ``/generate`` 429."""
    from src.modules.whatsapp.domain.models import ReplyDraft

    conv = await _seed_conversation(wa_repo, wa_account.id)
    inbound = next(m for m in wa_repo.messages.values() if m.direction is Direction.INBOUND)
    assert inbound.kind is MessageKind.TEXT
    await wa_repo.create_draft(
        ReplyDraft(
            conversation_id=conv.id,
            brand_id=BRAND_ID,
            in_reply_to_message_id=inbound.id,
            suggested_body="x",
            provenance={"kind": "ai_suggested", "prompt_tokens": 10**9, "completion_tokens": 1},
            created_at=datetime.now(tz=UTC),
        )
    )
    with TestClient(wa_app) as client:
        owner = auth_headers(OWNER_ID)
        item = client.post(
            f"{API}/brands/{BRAND_ID}/content/items",
            json={"channel": "facebook_page", "body": "نص"},
            headers=owner,
        )
        assert item.status_code == 201, item.text
        res = client.post(
            f"{API}/brands/{BRAND_ID}/content/items/{item.json()['id']}/generate",
            json={"brief": "اكتب منشوراً عن العرض"},
            headers=owner,
        )
        assert res.status_code == 429, res.text
        assert res.json()["detail"]["error"] == "generation_budget_exceeded"
        assert "Retry-After" in res.headers
        again = _suggest(client, owner, conv.id)
        assert (
            again.status_code == 409
        )  # pending draft exists (A-5 uniqueness) — budget not reached
        _ = uuid4()
