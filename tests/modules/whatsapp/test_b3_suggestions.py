"""SPRINT-09 B-3 — AI reply suggestion behind human approval (B3-U1..U7) on the fakes.

The AI gateway is the ``fake`` provider (settings default); the WhatsApp Cloud API is
``respx``-mocked so every test can assert the exact number of outbound calls (zero for
``suggest``, one for ``approve``).
"""

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
from src.modules.whatsapp import signals
from src.modules.whatsapp.domain.models import (
    DeliveryStatus,
    Direction,
    DraftStatus,
    Message,
    MessageKind,
    Signal,
)
from src.modules.whatsapp.prompts import (
    REPLY_MAX_CHARS,
    REPLY_PROMPT_VERSION,
    reply_system_prompt,
    reply_user_prompt,
)
from src.modules.whatsapp.schemas import PUBLIC_DRAFT_PROVENANCE_KEYS

from tests.conftest import AGENCY_ID, BRAND_ID, OWNER_ID, STRANGER_ID, USER_BY_TENANT, grant
from tests.fakes import FakeContentRepository, FakeRateLimiter, FakeStore, FakeWhatsAppRepository
from tests.modules.whatsapp.conftest import CUSTOMER_WA_ID, PHONE_NUMBER_ID
from tests.modules.whatsapp.test_b2_inbox import _ok, _seed_conversation

API = "/api/v1"
BASE = f"{API}/brands/{BRAND_ID}/whatsapp"
MESSAGES = f"/{PHONE_NUMBER_ID}/messages"


@pytest.fixture
def roles(store: FakeStore) -> None:
    grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    grant(store, BRAND_ID, STRANGER_ID, GrantRole.VIEWER)


async def _add_outbound(repo: FakeWhatsAppRepository, conv_id: UUID, *, body: str = "ok") -> None:
    now = datetime.now(tz=UTC)
    await repo.insert_outbound(
        Message(
            conversation_id=conv_id,
            brand_id=BRAND_ID,
            direction=Direction.OUTBOUND,
            kind=MessageKind.TEXT,
            body=body,
            delivery_status=DeliveryStatus.SENT,
            sent_by_user_id=USER_BY_TENANT[OWNER_ID],
            wamid=f"wamid.OUT.{uuid4()}",
            created_at=now,
        )
    )


def _suggest(client: TestClient, headers: dict[str, str], conv_id: UUID, **body: object):
    return client.post(f"{BASE}/conversations/{conv_id}/suggest-reply", json=body, headers=headers)


def _approve(client: TestClient, headers: dict[str, str], draft_id: str, **body: object):
    return client.post(f"{BASE}/reply-drafts/{draft_id}/approve", json=body, headers=headers)


def _reject(client: TestClient, headers: dict[str, str], draft_id: str, reason: str = "off_tone"):
    return client.post(
        f"{BASE}/reply-drafts/{draft_id}/reject", json={"reason": reason}, headers=headers
    )


# ---------------------------------------------------------------------- B3-U1 ---
async def test_b3_u1_suggest_reply_creates_draft_without_any_whatsapp_call(
    wa_app: FastAPI,
    wa_repo: FakeWhatsAppRepository,
    auth_headers,
    roles: None,
    wa_account,
    graph_mock: respx.MockRouter,
) -> None:
    """B3-U1 — ``suggest-reply`` ⇒ 201 draft ``suggested`` with ``provenance.kind ==
    "ai_suggested"`` and ``prompt_version == "wa-reply-v1"``; **zero** Cloud API calls;
    ``counters["wa_suggestions"]`` bumps; the draft is in reply to the last inbound."""
    route = graph_mock.post(MESSAGES).mock(return_value=_ok())
    conv = await _seed_conversation(wa_repo, wa_account.id, body="كم سعر الباقة الشهرية؟")
    before = counters.get("wa_suggestions")
    with TestClient(wa_app) as client:
        res = _suggest(client, auth_headers(AGENCY_ID), conv.id, tone="formal", language="ar")
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["status"] == "suggested" and body["final_body"] is None
        assert body["suggested_body"].startswith("[fake:reply_suggestion:")
        assert body["provenance"]["kind"] == "ai_suggested"
        assert body["provenance"]["prompt_version"] == REPLY_PROMPT_VERSION
        assert body["provenance"]["provider"] == "fake"  # MANAGER sees private keys
        assert body["provenance"]["tone"] == "formal" and body["provenance"]["language"] == "ar"
        last = await wa_repo.last_message(conv.id)
        assert last is not None and body["in_reply_to_message_id"] == str(last.id)
        # The conversation list flags the pending draft.
        listing = client.get(f"{BASE}/conversations", headers=auth_headers(OWNER_ID)).json()
        assert listing["items"][0]["pending_draft"] is True
    assert len(route.calls) == 0
    assert counters.get("wa_suggestions") == before + 1
    draft = wa_repo.drafts[UUID(body["id"])]
    assert draft.status is DraftStatus.SUGGESTED
    assert isinstance(draft.provenance["prompt_tokens"], int)


def test_b3_u1_prompts_pin_the_four_rules_and_truncate_transcript() -> None:
    system = reply_system_prompt("Nour Café", "auto", "friendly")
    assert "600" in system and "D-055" in system
    assert "Nour Café" in system and "friendly" in system
    for language in ("ar", "en"):
        assert reply_system_prompt("x", language, "formal") != system
    user = reply_user_prompt(
        [("inbound", f"m{i}") for i in range(10)] + [("outbound", None)], max_turns=6
    )
    assert "m5" in user and "m4" not in user  # only the last six of eleven turns
    assert "non-text message" in user  # None body rendered as placeholder
    assert reply_user_prompt([], max_turns=0).endswith("Recent conversation:\n")


# ---------------------------------------------------------------------- B3-U2 ---
async def test_b3_u2_suggest_reply_conflicts_and_rate_limit(
    wa_app: FastAPI,
    wa_repo: FakeWhatsAppRepository,
    auth_headers,
    roles: None,
    wa_account,
    limiter: FakeRateLimiter,
) -> None:
    """B3-U2 — last message outbound ⇒ 409 ``no_inbound_to_reply``; an existing ``suggested``
    draft ⇒ 409 ``draft_pending``; the 61st request in the hour ⇒ 429
    ``generation_rate_limited`` on bucket ``wa_suggest``; VIEWER ⇒ 403; unknown conversation
    ⇒ 404."""
    conv = await _seed_conversation(wa_repo, wa_account.id)
    answered = await _seed_conversation(wa_repo, wa_account.id, wa_id="201111111111")
    await _add_outbound(wa_repo, answered.id)
    with TestClient(wa_app) as client:
        owner = auth_headers(OWNER_ID)
        res = _suggest(client, owner, answered.id)
        assert res.status_code == 409 and res.json()["detail"]["error"] == "no_inbound_to_reply"

        first = _suggest(client, owner, conv.id)
        assert first.status_code == 201, first.text
        again = _suggest(client, owner, conv.id)
        assert again.status_code == 409 and again.json()["detail"]["error"] == "draft_pending"
        assert len([d for d in wa_repo.drafts.values() if d.conversation_id == conv.id]) == 1

        viewer = _suggest(client, auth_headers(STRANGER_ID), conv.id)
        assert viewer.status_code == 403
        assert _suggest(client, owner, uuid4()).status_code == 404

        # Rate limit: the limiter is hit once per *eligible* request (after the 409 guards).
        assert limiter.hits == [("wa_suggest", str(BRAND_ID))]
        _reject(client, owner, first.json()["id"])
        limiter.reject_after = 1
        limited = _suggest(client, owner, conv.id)
        assert limited.status_code == 429, limited.text
        assert limited.json()["detail"]["error"] == "generation_rate_limited"
        assert "Retry-After" in limited.headers
        assert limiter.hits == [("wa_suggest", str(BRAND_ID))] * 2


async def test_b3_u2_daily_budget_is_shared_with_content_generation(
    wa_app: FastAPI,
    wa_repo: FakeWhatsAppRepository,
    content_repo: FakeContentRepository,
    auth_headers,
    roles: None,
    wa_account,
) -> None:
    """The A-5 budget counts ``content_revisions`` **and** ``wa_reply_drafts`` provenance
    together: content usage alone can exhaust the WhatsApp suggestions (429
    ``generation_budget_exceeded`` — the SPRINT-07 code)."""
    conv = await _seed_conversation(wa_repo, wa_account.id)
    content_repo.token_usage_override = 10**9
    with TestClient(wa_app) as client:
        res = _suggest(client, auth_headers(OWNER_ID), conv.id)
        assert res.status_code == 429, res.text
        detail = res.json()["detail"]
        assert detail["error"] == "generation_budget_exceeded"
        assert detail["used"] >= 10**9 and detail["budget"] > 0
    assert not wa_repo.drafts


# ---------------------------------------------------------------------- B3-U3 ---
async def test_b3_u3_three_signals_and_edit_distance_ratio(
    wa_app: FastAPI,
    wa_repo: FakeWhatsAppRepository,
    auth_headers,
    roles: None,
    wa_account,
    graph_mock: respx.MockRouter,
) -> None:
    """B3-U3 — approve unchanged ⇒ ``approved`` + ``approved_unchanged`` (ratio 0); approve
    with one word changed ⇒ ``edited`` + ``approved_after_edit`` with ``0 < ratio < 0.3``;
    full replacement ⇒ ``ratio > 0.7``; reject ⇒ ``rejected`` + reason. Each approval sends
    exactly one message linked by ``reply_draft_id`` and with the human actor."""
    route = graph_mock.post(MESSAGES).mock(return_value=_ok())
    convs = [
        await _seed_conversation(wa_repo, wa_account.id, wa_id=f"20100000000{i}") for i in range(4)
    ]
    with TestClient(wa_app) as client:
        manager = auth_headers(AGENCY_ID)
        drafts = [_suggest(client, manager, c.id).json() for c in convs]

        unchanged = _approve(client, manager, drafts[0]["id"])
        assert unchanged.status_code == 200, unchanged.text
        assert unchanged.json()["draft"]["status"] == "approved"
        assert unchanged.json()["delivery_status"] == "sent"
        msg = unchanged.json()["message"]
        assert msg["reply_draft_id"] == drafts[0]["id"]
        assert msg["sent_by_user_id"] == str(USER_BY_TENANT[AGENCY_ID])
        assert msg["body"] == drafts[0]["suggested_body"]
        assert len(route.calls) == 1

        same_text = _approve(
            client, manager, drafts[1]["id"], final_body=drafts[1]["suggested_body"]
        )
        assert same_text.json()["draft"]["status"] == "approved"

        tweaked = drafts[2]["suggested_body"].replace("reply_suggestion", "reply_suggestions", 1)
        edited = _approve(client, manager, drafts[2]["id"], final_body=tweaked)
        assert edited.status_code == 200, edited.text
        assert edited.json()["draft"]["status"] == "edited"
        assert edited.json()["draft"]["final_body"] == tweaked
        assert edited.json()["message"]["body"] == tweaked
        assert len(route.calls) == 3

        replaced = _approve(client, manager, drafts[3]["id"], final_body="شكراً لتواصلك معنا.")
        assert replaced.json()["draft"]["status"] == "edited"
        assert len(route.calls) == 4

        # Decided drafts cannot be decided twice.
        again = _approve(client, manager, drafts[0]["id"])
        assert again.status_code == 409 and again.json()["detail"]["error"] == "draft_not_pending"
        assert again.json()["detail"]["current_status"] == "approved"
        assert _reject(client, manager, drafts[0]["id"]).status_code == 409

    rows = await wa_repo.learning_signals(BRAND_ID)
    assert [r["signal"] for r in rows] == [
        "approved_unchanged",
        "approved_unchanged",
        "approved_after_edit",
        "approved_after_edit",
    ]
    assert rows[0]["edit_distance_ratio"] == 0.0
    assert 0 < rows[2]["edit_distance_ratio"] < 0.3
    assert rows[3]["edit_distance_ratio"] > 0.7
    assert rows[3]["features"]["language"] == "ar"  # full Arabic replacement
    # The fake provider echoes the bilingual transcript ⇒ the lightly edited draft is "mixed".
    assert rows[2]["features"]["language"] == "mixed"
    assert all(r["rejection_reason"] is None for r in rows)
    # Every outbound sent through approve carries the human actor (§2-0 أ).
    outbound = [m for m in wa_repo.messages.values() if m.direction is Direction.OUTBOUND]
    assert len(outbound) == 4 and all(m.sent_by_user_id is not None for m in outbound)
    assert {m.reply_draft_id for m in outbound} == {UUID(d["id"]) for d in drafts}


async def test_b3_u3_reject_records_reason_signal_and_sends_nothing(
    wa_app: FastAPI,
    wa_repo: FakeWhatsAppRepository,
    auth_headers,
    roles: None,
    wa_account,
    graph_mock: respx.MockRouter,
) -> None:
    route = graph_mock.post(MESSAGES).mock(return_value=_ok())
    conv = await _seed_conversation(wa_repo, wa_account.id)
    with TestClient(wa_app) as client:
        owner = auth_headers(OWNER_ID)
        draft = _suggest(client, owner, conv.id).json()
        bad = _reject(client, owner, draft["id"], reason="angry")
        assert bad.status_code == 422
        res = _reject(client, owner, draft["id"], reason="factually_wrong")
        assert res.status_code == 200, res.text
        assert res.json()["status"] == "rejected"
        assert res.json()["rejection_reason"] == "factually_wrong"
        assert res.json()["decided_by_user_id"] == str(USER_BY_TENANT[OWNER_ID])
        # A new suggestion is allowed once the pending one is rejected.
        assert _suggest(client, owner, conv.id).status_code == 201
        assert _reject(client, owner, str(uuid4())).status_code == 404
    rows = await wa_repo.learning_signals(BRAND_ID)
    assert [(r["signal"], r["rejection_reason"]) for r in rows] == [
        ("rejected", "factually_wrong")
    ]
    assert len(route.calls) == 0
    assert counters.get("wa_draft_rejected") >= 1


def test_b3_u3_signal_feature_functions_are_pure() -> None:
    assert signals.edit_distance_ratio("abc", "abc") == 0.0
    assert signals.edit_distance_ratio("hello there", "hello here") < 0.3
    assert signals.edit_distance_ratio("hello there", "مرحبا بك") > 0.7
    assert signals.language("مرحبا بك في المتجر") == "ar"
    assert signals.language("Hello and welcome") == "en"
    assert signals.language("Hello مرحبا welcome بك friend") == "mixed"
    assert signals.language("123 ... !!") == "other"
    assert signals.has_question("هل تريد؟") and signals.has_question("ok?")
    assert not signals.has_question("no marks here")
    assert signals.has_cta("اطلب الآن") and signals.has_cta("Please BOOK a slot")
    assert not signals.has_cta("thanks for the info")
    feats = signals.features("short", "a much longer final text?")
    assert feats == {
        "suggested_len": 5,
        "final_len": 25,
        "language": "en",
        "has_question": True,
        "has_cta": False,
    }
    assert signals.features("abc", None)["final_len"] == 3


# ---------------------------------------------------------------------- B3-U4 ---
async def test_b3_u4_signal_rows_never_contain_draft_text(
    wa_app: FastAPI,
    wa_repo: FakeWhatsAppRepository,
    auth_headers,
    roles: None,
    wa_account,
    graph_mock: respx.MockRouter,
) -> None:
    """B3-U4 (D-069 guard) — the JSON of every signal row contains no run of 12 consecutive
    characters taken from ``suggested_body`` or ``final_body``."""
    graph_mock.post(MESSAGES).mock(return_value=_ok())
    conv = await _seed_conversation(wa_repo, wa_account.id)
    final = "أهلاً بك! يسعدنا مساعدتك، هل يمكنك توضيح الباقة التي تقصدها؟"
    with TestClient(wa_app) as client:
        owner = auth_headers(OWNER_ID)
        draft = _suggest(client, owner, conv.id).json()
        assert _approve(client, owner, draft["id"], final_body=final).status_code == 200
    stored = wa_repo.drafts[UUID(draft["id"])]
    texts = [stored.suggested_body, stored.final_body or ""]
    for signal in wa_repo.signals.values():
        blob = json.dumps(signal.model_dump(mode="json"), ensure_ascii=False)
        for text in texts:
            for i in range(len(text) - 11):
                assert text[i : i + 12] not in blob, text[i : i + 12]
        assert set(signal.features) == {
            "suggested_len",
            "final_len",
            "language",
            "has_question",
            "has_cta",
        }


# ---------------------------------------------------------------------- B3-U5 ---
async def test_b3_u5_approve_with_closed_window_is_422_draft_stays_approved_unsent(
    wa_app: FastAPI,
    wa_repo: FakeWhatsAppRepository,
    auth_headers,
    roles: None,
    wa_account,
    graph_mock: respx.MockRouter,
) -> None:
    """B3-U5 — suggestion is possible after the window closed (reading is fine) but approving
    ⇒ 422 ``whatsapp_window_closed`` + ``template_required``; the draft is ``approved`` with
    its signal, **no** message row, **no** Cloud API call."""
    route = graph_mock.post(MESSAGES).mock(return_value=_ok())
    conv = await _seed_conversation(wa_repo, wa_account.id, inbound_ago=timedelta(hours=30))
    with TestClient(wa_app) as client:
        owner = auth_headers(OWNER_ID)
        draft = _suggest(client, owner, conv.id).json()
        res = _approve(client, owner, draft["id"])
        assert res.status_code == 422, res.text
        assert res.json()["detail"] == {
            "error": "whatsapp_window_closed",
            "template_required": True,
        }
    assert wa_repo.drafts[UUID(draft["id"])].status is DraftStatus.APPROVED
    assert not [m for m in wa_repo.messages.values() if m.direction is Direction.OUTBOUND]
    assert len(route.calls) == 0
    rows = await wa_repo.learning_signals(BRAND_ID)
    assert [r["signal"] for r in rows] == ["approved_unchanged"]


async def test_b3_u5_approve_send_failures_map_like_b2_and_keep_draft_decided(
    wa_app: FastAPI,
    wa_repo: FakeWhatsAppRepository,
    auth_headers,
    roles: None,
    wa_account,
    graph_mock: respx.MockRouter,
) -> None:
    """Ambiguous Graph outcome ⇒ 202 with ``delivery_status=ambiguous`` (one POST, D-070);
    the approval itself is final either way."""
    route = graph_mock.post(MESSAGES).mock(return_value=httpx.Response(500, json={"error": {}}))
    conv = await _seed_conversation(wa_repo, wa_account.id)
    with TestClient(wa_app) as client:
        owner = auth_headers(OWNER_ID)
        draft = _suggest(client, owner, conv.id).json()
        res = _approve(client, owner, draft["id"])
        assert res.status_code == 202, res.text
        assert res.json()["delivery_status"] == "ambiguous"
        assert res.json()["draft"]["status"] == "approved"
    assert len(route.calls) == 1


# ---------------------------------------------------------------------- B3-U6 ---
async def test_b3_u6_viewer_sees_suggested_body_but_no_private_provenance_and_cannot_decide(
    wa_app: FastAPI,
    wa_repo: FakeWhatsAppRepository,
    auth_headers,
    roles: None,
    wa_account,
) -> None:
    """B3-U6 — VIEWER reads the thread with the draft's ``suggested_body`` but only the public
    provenance keys (no ``provider`` / ``model`` / ``prompt_hash`` / tokens); ``approve`` and
    ``reject`` ⇒ 403 ``insufficient_role``; MANAGER sees the private keys."""
    conv = await _seed_conversation(wa_repo, wa_account.id)
    with TestClient(wa_app) as client:
        owner, viewer = auth_headers(OWNER_ID), auth_headers(STRANGER_ID)
        draft = _suggest(client, owner, conv.id).json()
        thread = client.get(f"{BASE}/conversations/{conv.id}/messages", headers=viewer)
        assert thread.status_code == 200, thread.text
        [seen] = thread.json()["reply_drafts"]
        assert seen["suggested_body"] == draft["suggested_body"]
        assert set(seen["provenance"]) <= PUBLIC_DRAFT_PROVENANCE_KEYS
        assert "provider" not in seen["provenance"] and "prompt_hash" not in seen["provenance"]
        assert seen["provenance"]["prompt_version"] == REPLY_PROMPT_VERSION
        denied = _approve(client, viewer, draft["id"])
        assert (
            denied.status_code == 403 and denied.json()["detail"]["error"] == "insufficient_role"
        )
        assert _reject(client, viewer, draft["id"]).status_code == 403
        manager = client.get(f"{BASE}/conversations/{conv.id}/messages", headers=owner).json()
        assert manager["reply_drafts"][0]["provenance"]["provider"] == "fake"
    assert wa_repo.drafts[UUID(draft["id"])].status is DraftStatus.SUGGESTED


# ---------------------------------------------------------------------- B3-U7 ---
async def test_b3_u7_newer_inbound_expires_pending_draft_with_signal(
    wa_app: FastAPI,
    wa_repo: FakeWhatsAppRepository,
    auth_headers,
    roles: None,
    wa_account,
) -> None:
    """B3-U7 — a newer inbound message (webhook) flips ``suggested`` ⇒ ``expired`` and writes
    an ``expired`` signal; approving it afterwards ⇒ 409 ``draft_not_pending``; a manual
    reply does the same for a fresh draft."""
    from tests.modules.whatsapp.conftest import load_payload
    from tests.modules.whatsapp.test_b1_webhook import _post as post_signed

    conv = await _seed_conversation(wa_repo, wa_account.id)
    with TestClient(wa_app) as client:
        owner = auth_headers(OWNER_ID)
        draft = _suggest(client, owner, conv.id).json()
        payload = load_payload("inbound_text")
        value = payload["entry"][0]["changes"][0]["value"]
        value["metadata"]["phone_number_id"] = PHONE_NUMBER_ID
        value["contacts"][0]["wa_id"] = CUSTOMER_WA_ID
        value["messages"][0]["from"] = CUSTOMER_WA_ID
        value["messages"][0]["id"] = f"wamid.NEW.{uuid4()}"
        value["messages"][0]["timestamp"] = str(int(datetime.now(tz=UTC).timestamp()))
        res = post_signed(client, payload)
        assert res.status_code == 200, res.text
        assert res.json()["expired_drafts"] == [draft["id"]]
        stale = _approve(client, owner, draft["id"])
        assert stale.status_code == 409 and stale.json()["detail"]["current_status"] == "expired"

        # Manual reply path: a fresh draft expires when a human sends text directly.
        with respx.mock(base_url="https://graph.facebook.com") as graph:
            graph.post(path__regex=r".*/messages$").mock(return_value=_ok("wamid.MANUAL"))
            fresh = _suggest(client, owner, conv.id).json()
            sent = client.post(
                f"{BASE}/conversations/{conv.id}/messages", json={"body": "رد يدوي"}, headers=owner
            )
            assert sent.status_code == 201, sent.text
    assert wa_repo.drafts[UUID(draft["id"])].status is DraftStatus.EXPIRED
    assert wa_repo.drafts[UUID(fresh["id"])].status is DraftStatus.EXPIRED
    rows = await wa_repo.learning_signals(BRAND_ID)
    assert [r["signal"] for r in rows] == ["expired", "expired"]
    assert all(r["edit_distance_ratio"] is None for r in rows)
    assert {s.reply_draft_id for s in wa_repo.signals.values()} == {
        UUID(draft["id"]),
        UUID(fresh["id"]),
    }
    assert Signal.EXPIRED.value == "expired" and REPLY_MAX_CHARS == 600
