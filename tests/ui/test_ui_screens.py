"""SPRINT-12.1 B-2/B-3 — Today and Inbox screens through the real API (fakes behind it)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import respx
from fastapi.testclient import TestClient
from src.modules.whatsapp.domain.models import NumberCap

from tests.conftest import BRAND_ID
from tests.fakes import FakeWhatsAppRepository
from tests.modules.pulse.test_pulse import FakePulseRepository
from tests.ui.conftest import (
    GRAPH_MESSAGES,
    INBOX,
    TODAY,
    csrf_of,
    login,
    seed_alert,
    seed_conversation,
)

MISSING = "00000000-0000-4000-8000-0000000000ff"


def _ok(wamid: str = "wamid.OUT1") -> httpx.Response:
    return httpx.Response(200, json={"messaging_product": "whatsapp", "messages": [{"id": wamid}]})


async def test_today_renders_four_cards_rtl_and_plan_bar(
    ui_client: TestClient,
    pulse_repo: FakePulseRepository,
    wa_repo: FakeWhatsAppRepository,
    wa_account,  # noqa: ANN001
) -> None:
    alert = seed_alert(pulse_repo)
    soon = await seed_conversation(wa_repo, wa_account.id)  # closes in 3h
    await seed_conversation(
        wa_repo,
        wa_account.id,
        inbound_ago=timedelta(hours=2),
        wa_id="201111111111",
        profile_name="Far",
    )
    login(ui_client)
    res = ui_client.get(TODAY)
    assert res.status_code == 200, res.text
    html = res.text
    assert '<html lang="ar" dir="rtl">' in html
    for card in ("alerts", "windows", "pending", "playbooks"):
        assert f'data-card="{card}"' in html
    assert f'data-alert="{alert.id}"' in html
    assert f"conversation={soon.id}" in html and "Far" not in html  # 22h away ⇒ not closing
    assert 'data-plan="trial"' in html
    assert 'hx-trigger="every 30s"' in html


def test_today_partial_for_htmx_polling(ui_client: TestClient) -> None:
    login(ui_client)
    res = ui_client.get(TODAY, headers={"HX-Request": "true"})
    assert res.status_code == 200
    assert "<html" not in res.text and res.text.count("data-card=") == 4


def test_ack_alert_from_today(ui_client: TestClient, pulse_repo: FakePulseRepository) -> None:
    alert = seed_alert(pulse_repo)
    login(ui_client)
    token = csrf_of(ui_client.get(TODAY).text)
    res = ui_client.post(f"{TODAY}/alerts/{alert.id}/ack", data={"csrf": token})
    assert res.status_code == 200
    assert pulse_repo.alerts[alert.id].acknowledged_at is not None
    assert f'data-alert="{alert.id}"' not in res.text  # no longer open
    # acking an unknown alert ⇒ translated flash, never a 5xx
    res = ui_client.post(f"{TODAY}/alerts/{MISSING}/ack", data={"csrf": token})
    assert res.status_code == 200 and "data-flash" in res.text


def test_approve_unknown_item_from_today_shows_flash(ui_client: TestClient) -> None:
    login(ui_client)
    token = csrf_of(ui_client.get(TODAY).text)
    res = ui_client.post(f"{TODAY}/items/{MISSING}/approve", data={"csrf": token})
    assert res.status_code == 200 and "data-flash" in res.text


async def test_inbox_lists_threads_and_autoescapes_profile_name(
    ui_client: TestClient,
    wa_repo: FakeWhatsAppRepository,
    wa_account,  # noqa: ANN001
) -> None:
    evil = await seed_conversation(
        wa_repo, wa_account.id, profile_name="<script>alert(1)</script>", body="<b>x</b>"
    )
    login(ui_client)
    res = ui_client.get(INBOX)
    assert res.status_code == 200 and f'data-conversation="{evil.id}"' in res.text
    assert "<script>alert(1)</script>" not in res.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in res.text
    res = ui_client.get(f"{INBOX}?conversation={evil.id}")
    assert res.status_code == 200
    assert "<b>x</b>" not in res.text and "&lt;b&gt;x&lt;/b&gt;" in res.text
    assert 'data-lead-stage="new"' in res.text and 'data-form="reply"' in res.text


async def test_reply_sends_via_graph_and_renders_outbound(
    ui_client: TestClient,
    wa_repo: FakeWhatsAppRepository,
    wa_account,  # noqa: ANN001
    graph_mock: respx.MockRouter,
) -> None:
    route = graph_mock.post(GRAPH_MESSAGES).mock(return_value=_ok())
    conv = await seed_conversation(wa_repo, wa_account.id)
    login(ui_client)
    token = csrf_of(ui_client.get(f"{INBOX}?conversation={conv.id}").text)
    res = ui_client.post(
        f"{INBOX}/{conv.id}/reply",
        data={"csrf": token, "body": "thanks!"},
        headers={"HX-Request": "true"},
    )
    assert res.status_code == 200, res.text
    assert len(route.calls) == 1
    assert 'data-direction="outbound"' in res.text and "thanks!" in res.text
    assert "<html" not in res.text  # htmx swap ⇒ partial only
    assert "data-flash" not in res.text


async def test_kill_switch_423_renders_translated_message_not_500(
    ui_client: TestClient,
    wa_repo: FakeWhatsAppRepository,
    wa_account,  # noqa: ANN001
    graph_mock: respx.MockRouter,
) -> None:
    route = graph_mock.post(GRAPH_MESSAGES).mock(return_value=_ok())
    conv = await seed_conversation(wa_repo, wa_account.id)
    await wa_repo.upsert_number_cap(
        NumberCap(account_id=wa_account.id, kill_switch=True, updated_at=datetime.now(tz=UTC))
    )
    login(ui_client)
    token = csrf_of(ui_client.get(f"{INBOX}?conversation={conv.id}").text)
    res = ui_client.post(f"{INBOX}/{conv.id}/reply", data={"csrf": token, "body": "hi"})
    assert res.status_code == 200, res.text
    assert "data-flash" in res.text and "wa_number_disabled" not in res.text
    assert "\u0645\u0641\u062a\u0627\u062d" in res.text  # the translated kill-switch copy
    assert len(route.calls) == 0


async def test_lead_stage_swap_shows_new_stage_immediately(
    ui_client: TestClient,
    wa_repo: FakeWhatsAppRepository,
    wa_account,  # noqa: ANN001
) -> None:
    conv = await seed_conversation(wa_repo, wa_account.id)
    login(ui_client)
    token = csrf_of(ui_client.get(f"{INBOX}?conversation={conv.id}").text)
    res = ui_client.post(
        f"{INBOX}/{conv.id}/stage",
        data={"csrf": token, "lead_stage": "lost", "objection_code": "price"},
        headers={"HX-Request": "true"},
    )
    assert res.status_code == 200, res.text
    assert 'data-lead-stage="lost"' in res.text
    assert wa_repo.conversations[conv.id].lead_stage.value == "lost"
    # an invalid stage ⇒ 422 from the API ⇒ translated flash, stage unchanged
    res = ui_client.post(f"{INBOX}/{conv.id}/stage", data={"csrf": token, "lead_stage": "bogus"})
    assert res.status_code == 200 and "data-flash" in res.text
    assert wa_repo.conversations[conv.id].lead_stage.value == "lost"


async def test_suggest_prefills_reply_box(
    ui_client: TestClient,
    wa_repo: FakeWhatsAppRepository,
    wa_account,  # noqa: ANN001
    graph_mock: respx.MockRouter,
) -> None:
    route = graph_mock.post(GRAPH_MESSAGES).mock(return_value=_ok())
    conv = await seed_conversation(wa_repo, wa_account.id)
    login(ui_client)
    token = csrf_of(ui_client.get(f"{INBOX}?conversation={conv.id}").text)
    res = ui_client.post(f"{INBOX}/{conv.id}/suggest", data={"csrf": token})
    assert res.status_code == 200, res.text
    assert "[fake:reply_suggestion:" in res.text  # fake gateway output in the textarea
    assert len(route.calls) == 0  # suggestions never hit WhatsApp


def test_inbox_for_brand_without_account_is_empty_not_error(ui_client: TestClient) -> None:
    login(ui_client)
    res = ui_client.get(INBOX)
    assert res.status_code == 200 and "data-empty" in res.text
    assert str(BRAND_ID) in res.text
