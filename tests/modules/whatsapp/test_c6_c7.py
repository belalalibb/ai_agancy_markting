"""SPRINT-11 C-6 / C-7 — learning-consent gating of ``wa_learning_signals`` (D-080) and
Click-to-WhatsApp ``referral`` persistence/exposure on inbound messages."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.modules.whatsapp.domain.models import DraftStatus
from src.modules.whatsapp.webhook import REFERRAL_KEYS, REFERRAL_MAX_CHARS

from tests.conftest import BRAND_ID, OWNER_ID
from tests.fakes import FakeStore, FakeWhatsAppRepository
from tests.modules.whatsapp.conftest import CUSTOMER_WA_ID, PHONE_NUMBER_ID, load_payload
from tests.modules.whatsapp.test_b1_webhook import _post as post_signed
from tests.modules.whatsapp.test_b2_inbox import _ok, _seed_conversation
from tests.modules.whatsapp.test_b3_suggestions import (
    BASE,
    MESSAGES,
    _approve,
    _reject,
    _suggest,
)


def _set_consent(store: FakeStore, value: str) -> None:
    brand = store.brands[BRAND_ID]
    store.brands[BRAND_ID] = brand.model_copy(update={"learning_consent": value})


def _inbound(
    *, wamid: str | None = None, referral: dict | None = None, wa_id: str = CUSTOMER_WA_ID
):
    payload = load_payload("inbound_text")
    value = payload["entry"][0]["changes"][0]["value"]
    value["metadata"]["phone_number_id"] = PHONE_NUMBER_ID
    value["contacts"][0]["wa_id"] = wa_id
    msg = value["messages"][0]
    msg["from"] = wa_id
    msg["id"] = wamid or f"wamid.C7.{uuid4()}"
    msg["timestamp"] = str(int(datetime.now(tz=UTC).timestamp()))
    if referral is not None:
        msg["referral"] = referral
    return payload


# ---------------------------------------------------------------------- C-6 ---
@pytest.mark.parametrize(("consent", "expected"), [("signals_only", 3), ("none", 0)])
async def test_c6_wa_signals_follow_learning_consent(
    wa_app: FastAPI,
    wa_repo: FakeWhatsAppRepository,
    store: FakeStore,
    auth_headers,
    wa_account,
    graph_mock: respx.MockRouter,
    consent: str,
    expected: int,
) -> None:
    """approve / reject / expire each decide the draft normally; the **signal row** is written
    only when ``brands.learning_consent`` allows it (``none`` ⇒ 0 rows, drafts still decided)."""
    _set_consent(store, consent)
    graph_mock.post(MESSAGES).mock(return_value=_ok())
    convs = [
        await _seed_conversation(wa_repo, wa_account.id, wa_id=f"20166600000{i}") for i in range(3)
    ]
    with TestClient(wa_app) as client:
        owner = auth_headers(OWNER_ID)
        drafts = []
        for conv in convs:
            res = _suggest(client, owner, conv.id)
            assert res.status_code == 201, res.text
            drafts.append(res.json())

        approved = _approve(client, owner, drafts[0]["id"])
        assert approved.status_code == 200, approved.text
        rejected = _reject(client, owner, drafts[1]["id"], reason="off_tone")
        assert rejected.status_code == 200, rejected.text
        expired = post_signed(client, _inbound(wa_id="201666000002"))
        assert expired.status_code == 200, expired.text
        assert expired.json()["expired_drafts"] == [drafts[2]["id"]]

    statuses = {wa_repo.drafts[UUID(d["id"])].status for d in drafts}
    assert statuses == {DraftStatus.APPROVED, DraftStatus.REJECTED, DraftStatus.EXPIRED}
    rows = await wa_repo.learning_signals(BRAND_ID)
    assert len(rows) == expected
    if expected:
        assert sorted(r["signal"] for r in rows) == ["approved_unchanged", "expired", "rejected"]


# ---------------------------------------------------------------------- C-7 ---
def test_c7_webhook_stores_trimmed_referral_and_exposes_it(
    wa_app: FastAPI, wa_repo: FakeWhatsAppRepository, wa_account, auth_headers
) -> None:
    """A CTWA inbound carries ``referral`` ⇒ stored with known keys only, values coerced to
    text and trimmed to 512 chars; exposed on ``MessagePublic``; non-CTWA inbound ⇒ ``null``."""
    long_body = "x" * (REFERRAL_MAX_CHARS + 50)
    raw = {
        "source_url": "https://fb.me/ad123",
        "source_id": 987654321,
        "source_type": "ad",
        "headline": "عرض اليوم",
        "body": long_body,
        "ctwa_clid": "clid-abc",
        "image_url": "https://cdn/x.jpg",  # unknown key — dropped
        "nested": {"a": 1},  # non-scalar — dropped
    }
    with TestClient(wa_app) as client:
        first = post_signed(client, _inbound(wamid="wamid.C7.REF", referral=raw))
        assert first.status_code == 200, first.text
        plain = post_signed(client, _inbound(wamid="wamid.C7.PLAIN"))
        assert plain.status_code == 200, plain.text

        conv_id = next(iter(wa_repo.conversations))
        res = client.get(
            f"{BASE}/conversations/{conv_id}/messages", headers=auth_headers(OWNER_ID)
        )
        assert res.status_code == 200, res.text
        by_wamid = {m["wamid"]: m for m in res.json()["items"]}

    stored = by_wamid["wamid.C7.REF"]["referral"]
    assert set(stored) <= set(REFERRAL_KEYS)
    assert "image_url" not in stored and "nested" not in stored
    assert stored["source_id"] == "987654321"
    assert stored["body"] == "x" * REFERRAL_MAX_CHARS
    assert stored["headline"] == "عرض اليوم"
    assert by_wamid["wamid.C7.PLAIN"]["referral"] is None
