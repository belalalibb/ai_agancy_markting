"""SPRINT-13 Part B on PostgreSQL — check (9) ``wa_monthly_service_80pct`` thresholds and
monthly idempotency (§3-10), the webhook ``pricing`` fill of ``wa_messages.billable`` /
``pricing_category`` (B-1) and the OWNER account view with caps + quality (B-2)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from src.core.security import TokenEncryptionService
from src.modules.whatsapp.webhook import compute_signature
from tests.conftest import BRAND_ID, OWNER_ID, OWNER_USER_ID
from tests.integration.conftest import TEST_DATABASE_URL, integration_headers
from tests.integration.test_sprint12_b_pg import (
    APP_SECRET,
    PNID,
    _conversation,
    _pulse_run,
    _rows,
    _run,
    _seed_world,
    _wa_account,
)

pytestmark = pytest.mark.integration

API = "/api/v1"
WA_WEBHOOK = f"{API}/webhooks/whatsapp"
MONTH = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)  # mid-month ⇒ ``ago`` stays in the month


def _billable_outbound(conv_id: UUID, count: int, *, at: datetime, billable: bool | None) -> None:
    """``count`` outbound rows at ``at``; ``billable`` NULL / TRUE / FALSE as given."""

    async def _go() -> None:
        eng = create_async_engine(TEST_DATABASE_URL)
        try:
            async with eng.begin() as conn:
                rows = [
                    {
                        "id": uuid4(),
                        "c": conv_id,
                        "b": BRAND_ID,
                        "w": f"wamid.M.{uuid4()}",
                        "u": OWNER_USER_ID,
                        "at": at,
                        "bill": billable,
                    }
                    for _ in range(count)
                ]
                await conn.execute(
                    text(
                        "INSERT INTO wa_messages (id, conversation_id, brand_id, direction, "
                        "wamid, kind, body, delivery_status, sent_by_user_id, created_at, "
                        "billable) VALUES (:id, :c, :b, 'outbound', :w, 'text', 'reply', "
                        "'sent', :u, :at, :bill)"
                    ),
                    rows,
                )
        finally:
            await eng.dispose()

    asyncio.run(_go())


def _alerts(kind: str) -> list[tuple]:
    return _rows(
        "SELECT payload, severity FROM pulse_alerts WHERE kind = :k ORDER BY created_at",
        {"k": kind},
    )


# ------------------------------------------------------------------ B-1 (9) ------
def test_b1_monthly_service_800_threshold_idempotent_and_rearms_next_month(
    integration_encryption: TokenEncryptionService,
) -> None:
    """799 ⇒ nothing; 800 ⇒ one ``warning``; 801 in the same month ⇒ no second row; the next
    month ⇒ a new alert (``ref`` carries ``YYYY-MM``). ``billable = false`` never counts."""
    _seed_world()
    account_id = _wa_account(integration_encryption)
    conv = _conversation(account_id, inbound_ago=timedelta(hours=1), now=MONTH)
    _billable_outbound(conv, 700, at=MONTH - timedelta(days=3), billable=True)
    _billable_outbound(conv, 99, at=MONTH - timedelta(days=2), billable=None)  # conservative
    _billable_outbound(conv, 50, at=MONTH - timedelta(days=1), billable=False)  # excluded
    assert asyncio.run(_pulse_run(MONTH)) == [] or not _alerts("wa_monthly_service_80pct")
    assert _alerts("wa_monthly_service_80pct") == []  # 799 billable/NULL
    _billable_outbound(conv, 1, at=MONTH - timedelta(hours=1), billable=True)  # 800
    created = asyncio.run(_pulse_run(MONTH))
    assert [a.kind for a in created if a.kind == "wa_monthly_service_80pct"] == [
        "wa_monthly_service_80pct"
    ]
    ((payload, severity),) = _alerts("wa_monthly_service_80pct")
    assert severity == "warning"
    assert payload == {
        "ref": f"{account_id}:2026-11",
        "account_id": str(account_id),
        "count": 800,
        "cap": 1000,
    }
    _billable_outbound(conv, 1, at=MONTH, billable=True)  # 801, same month
    asyncio.run(_pulse_run(MONTH + timedelta(hours=2)))
    assert len(_alerts("wa_monthly_service_80pct")) == 1  # idempotent while open
    # Next month: the old alert is still open but the ref changes ⇒ a new one once ≥ 800.
    december = datetime(2026, 12, 10, 12, 0, tzinfo=UTC)
    assert asyncio.run(_pulse_run(december)) is not None
    assert len(_alerts("wa_monthly_service_80pct")) == 1  # 0 messages in December yet
    _billable_outbound(conv, 800, at=december - timedelta(days=1), billable=True)
    asyncio.run(_pulse_run(december))
    refs = [p["ref"] for p, _ in _alerts("wa_monthly_service_80pct")]
    assert refs == [f"{account_id}:2026-11", f"{account_id}:2026-12"]


def test_b1_status_webhook_fills_billable_and_pricing_category_on_pg(
    integration_app: FastAPI, integration_encryption: TokenEncryptionService
) -> None:
    _seed_world()
    account_id = _wa_account(integration_encryption)
    conv = _conversation(account_id, inbound_ago=timedelta(hours=1))
    wamid = f"wamid.OUT.{uuid4()}"
    _run(
        "INSERT INTO wa_messages (id, conversation_id, brand_id, direction, wamid, kind, body, "
        "delivery_status, sent_by_user_id, created_at) VALUES (:id, :c, :b, 'outbound', :w, "
        "'text', 'reply', 'sent', :u, now())",
        {"id": uuid4(), "c": conv, "b": BRAND_ID, "w": wamid, "u": OWNER_USER_ID},
    )
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_S12",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": PNID},
                            "statuses": [
                                {
                                    "id": wamid,
                                    "status": "delivered",
                                    "timestamp": str(int(datetime.now(tz=UTC).timestamp())),
                                    "recipient_id": "201234567890",
                                    "pricing": {
                                        "billable": True,
                                        "pricing_model": "CBP",
                                        "category": "service",
                                    },
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
    raw = json.dumps(payload).encode("utf-8")
    with TestClient(integration_app) as client:
        res = client.post(
            WA_WEBHOOK,
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": compute_signature(APP_SECRET, raw),
            },
        )
        assert res.status_code == 200 and res.json()["status_updates"] == 1, res.text
    assert _rows(
        "SELECT delivery_status, billable, pricing_category FROM wa_messages WHERE wamid = :w",
        {"w": wamid},
    ) == [("delivered", True, "service")]


# ------------------------------------------------------------------ B-2 ----------
def test_b2_owner_account_view_has_caps_quality_and_zero_secrets_on_pg(
    integration_app: FastAPI, integration_encryption: TokenEncryptionService
) -> None:
    _seed_world()
    account_id = _wa_account(integration_encryption)
    _run(
        "UPDATE whatsapp_accounts SET quality_rating = 'YELLOW', messaging_limit = 'TIER_1K' "
        "WHERE id = :id",
        {"id": account_id},
    )
    conv = _conversation(account_id, inbound_ago=timedelta(hours=1))
    _billable_outbound(conv, 3, at=datetime.now(tz=UTC) - timedelta(minutes=5), billable=True)
    settings = integration_app.state.settings
    with TestClient(integration_app) as client:
        res = client.get(
            f"{API}/brands/{BRAND_ID}/whatsapp/account",
            headers=integration_headers(settings, OWNER_ID),
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["quality_rating"] == "YELLOW" and body["messaging_limit"] == "TIER_1K"
        assert body["daily_reply_cap"] == 300 and body["kill_switch"] is False
        assert body["replies_today"] == 3
        assert not any("token" in k or "secret" in k or "hash" in k for k in body)
        assert "EAAS12" not in res.text and "s12-wa-app-secret" not in res.text
