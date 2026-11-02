"""SPRINT-11.1 A-2 (E2) — ``monthly_wa_conversations`` through the consumer-owned
``whatsapp/entitlements_port.py::CounterGuard`` bound by ``src/main.py``:

* a **signed webhook never answers 429** — the 51st new conversation is stored and listed
  under ``quota_exceeded`` in the 200 body;
* ``suggest-reply`` and the outbound sends refuse with **402 ``entitlement_quota_exceeded``**
  while the brand is over the ceiling; a duplicate delivery / existing conversation is never
  re-evaluated.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.modules.whatsapp.entitlements_port import get_counter_guard

from tests.conftest import BRAND_ID, OWNER_ID
from tests.fakes import FakeWhatsAppRepository
from tests.modules.whatsapp.conftest import CUSTOMER_WA_ID
from tests.modules.whatsapp.test_b1_webhook import _post as post_signed
from tests.modules.whatsapp.test_b2_inbox import _seed_conversation
from tests.modules.whatsapp.test_b3_suggestions import BASE
from tests.modules.whatsapp.test_c6_c7 import _inbound

QUOTA = {
    "error": "entitlement_quota_exceeded",
    "message": "monthly plan quota reached for monthly_wa_conversations",
    "counter": "monthly_wa_conversations",
    "limit": 50,
    "used": 51,
}


def test_a2_port_is_bound_in_main(wa_app: FastAPI) -> None:
    assert get_counter_guard in wa_app.dependency_overrides
    assert get_counter_guard() is None


def test_a2_webhook_never_429_flags_the_51st_conversation(
    wa_app: FastAPI,
    wa_account,  # noqa: ANN001
    wa_repo: FakeWhatsAppRepository,
) -> None:
    counter = wa_app.state.counter_reader
    # 50 conversations already this month, the 51st arrives ⇒ the fake reader now says 51.
    counter.used[(BRAND_ID, "monthly_wa_conversations")] = 51
    with TestClient(wa_app) as client:
        res = post_signed(client, _inbound(wamid="wamid.quota-1", wa_id="201666999001"))
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["inbound"] == 1
        conv = next(c for c in wa_repo.conversations.values() if c.wa_id == "201666999001")
        assert body["quota_exceeded"] == [str(conv.id)]  # stored anyway, flagged
        # Redelivery of the same message: duplicate, not re-flagged.
        dup = post_signed(client, _inbound(wamid="wamid.quota-1", wa_id="201666999001"))
        assert dup.status_code == 200 and dup.json()["duplicates"] == 1
        assert dup.json()["quota_exceeded"] == []
        # A second message into the *same* (existing) conversation is not a new conversation.
        again = post_signed(client, _inbound(wamid="wamid.quota-2", wa_id="201666999001"))
        assert again.status_code == 200 and again.json()["quota_exceeded"] == []


def test_a2_webhook_at_exactly_the_ceiling_is_not_flagged(
    wa_app: FastAPI,
    wa_account,  # noqa: ANN001
) -> None:
    wa_app.state.counter_reader.used[(BRAND_ID, "monthly_wa_conversations")] = 50
    with TestClient(wa_app) as client:
        res = post_signed(client, _inbound(wamid="wamid.quota-50", wa_id="201666999050"))
    assert res.status_code == 200 and res.json()["quota_exceeded"] == []


async def test_a2_suggest_and_sends_are_402_over_quota(
    wa_app: FastAPI,
    wa_account,  # noqa: ANN001
    wa_repo: FakeWhatsAppRepository,
    auth_headers: Callable[[UUID], dict[str, str]],
) -> None:
    owner = auth_headers(OWNER_ID)
    conv = await _seed_conversation(wa_repo, wa_account.id, wa_id=CUSTOMER_WA_ID)
    wa_app.state.counter_reader.used[(BRAND_ID, "monthly_wa_conversations")] = 51
    with TestClient(wa_app) as client:
        res = client.post(f"{BASE}/conversations/{conv.id}/suggest-reply", headers=owner)
        assert res.status_code == 402, res.text
        assert res.json()["detail"] == QUOTA
        res = client.post(
            f"{BASE}/conversations/{conv.id}/messages", json={"body": "hi"}, headers=owner
        )
        assert res.status_code == 402 and res.json()["detail"] == QUOTA
        res = client.post(
            f"{BASE}/conversations/{conv.id}/template",
            json={"name": "hello_world", "language_code": "en_US"},
            headers=owner,
        )
        assert res.status_code == 402 and res.json()["detail"] == QUOTA
        assert wa_repo.drafts == {}
        assert all(m.direction.value == "inbound" for m in wa_repo.messages.values())
        # Back within quota ⇒ the gate opens again (the suggestion itself is tested in B-3).
        wa_app.state.counter_reader.used[(BRAND_ID, "monthly_wa_conversations")] = 50
        res = client.post(f"{BASE}/conversations/{conv.id}/suggest-reply", headers=owner)
        assert res.status_code != 402
