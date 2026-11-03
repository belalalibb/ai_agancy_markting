"""SPRINT-12 Part B on PostgreSQL 17 — migration 0016 round-trip, the eight Pulse checks
against the real tables (idempotent until ack), WA caps (D-082: 301st ⇒ 429, kill ⇒ 423,
webhook never blocked), measured attribution from a Click-to-WhatsApp referral (D-079) and
the ``FEATURE_PULSE=false`` no-write guarantee."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
import respx
from alembic import command
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from src.core.config import Settings
from src.core.security import TokenEncryptionService
from src.modules.pulse.infrastructure.postgres_repository import PgPulseRepository
from src.modules.pulse.service import Pulse
from src.modules.whatsapp.webhook import compute_signature
from tests.conftest import AGENCY_ID, BRAND2_ID, BRAND_ID, OWNER_ID, OWNER_USER_ID, USER_BY_TENANT
from tests.integration.conftest import (
    TABLES,
    TEST_DATABASE_URL,
    alembic_config,
    integration_headers,
    seed_tenancy,
)

pytestmark = pytest.mark.integration

API = "/api/v1"
WA_WEBHOOK = f"{API}/webhooks/whatsapp"
WA_BASE = f"{API}/brands/{BRAND_ID}/whatsapp"
PNID = "PNID_S12_1001"
CUSTOMER = "201234567890"
APP_SECRET = "s12-wa-app-secret"  # noqa: S105 - test fixture
SYSTEM_TOKEN = "EAAS12SystemTokenLEAK"  # noqa: S105 - test fixture
NEW_TABLES = {"lead_attributions", "pulse_alerts", "wa_number_caps"}
NEW_CONSTRAINTS = {
    "ck_wa_conversations_lead_stage",
    "fk_lead_attributions_brand_id",
    "fk_lead_attributions_conversation_id",
    "uq_lead_attributions_conversation_source",
    "ck_lead_attributions_grade",
    "fk_pulse_alerts_brand_id",
    "ck_pulse_alerts_kind",
    "ck_pulse_alerts_severity",
    "fk_wa_number_caps_account_id",
    "ck_wa_number_caps_daily_reply_cap",
    "uq_brand_recommendations_share_token_hash",
}
NEW_COLUMNS = {
    ("wa_conversations", "lead_stage"),
    ("wa_conversations", "lead_stage_set_by_user_id"),
    ("wa_conversations", "lead_stage_at"),
    ("brand_recommendations", "share_token_hash"),
    ("brand_recommendations", "shared_at"),
}


# ------------------------------------------------------------------ sync helpers ---
def _run(sql: str, params: dict | None = None, *, scalar: bool = False) -> object:
    async def _go() -> object:
        eng = create_async_engine(TEST_DATABASE_URL)
        try:
            async with eng.begin() as conn:
                result = await conn.execute(text(sql), params or {})
                return result.scalar_one() if scalar else None
        finally:
            await eng.dispose()

    return asyncio.run(_go())


def _rows(sql: str, params: dict | None = None) -> list[tuple]:
    async def _go() -> list[tuple]:
        eng = create_async_engine(TEST_DATABASE_URL)
        try:
            async with eng.connect() as conn:
                return [tuple(r) for r in await conn.execute(text(sql), params or {})]
        finally:
            await eng.dispose()

    return asyncio.run(_go())


def _seed_world() -> None:
    async def _go() -> None:
        eng = create_async_engine(TEST_DATABASE_URL)
        try:
            async with eng.begin() as conn:
                await conn.execute(text(f"TRUNCATE {', '.join(TABLES)} CASCADE"))
            async with async_sessionmaker(eng, expire_on_commit=False)() as session:
                await seed_tenancy(session)
        finally:
            await eng.dispose()

    asyncio.run(_go())


def _wa_account(encryption: TokenEncryptionService, *, brand_id: UUID = BRAND_ID) -> UUID:
    from src.modules.whatsapp.service import hash_verify_token

    account_id = uuid4()
    _run(
        "INSERT INTO whatsapp_accounts (id, brand_id, phone_number_id, waba_id, display_phone, "
        "encrypted_system_token, encrypted_app_secret, verify_token_hash, status, "
        "created_by_user_id, created_at, updated_at) VALUES (:id, :b, :pnid, 'WABA_S12', "
        "'+1 555 000 1111', :tok, :sec, :vh, 'active', :u, now(), now())",
        {
            "id": account_id,
            "b": brand_id,
            "pnid": PNID,
            "tok": encryption.encrypt_token(SYSTEM_TOKEN),
            "sec": encryption.encrypt_token(APP_SECRET),
            "vh": hash_verify_token("s12-verify"),
            "u": USER_BY_TENANT[OWNER_ID],
        },
    )
    return account_id


def _conversation(
    account_id: UUID,
    *,
    inbound_ago: timedelta = timedelta(hours=1),
    brand_id: UUID = BRAND_ID,
    wa_id: str = CUSTOMER,
    now: datetime | None = None,
) -> UUID:
    conv_id = uuid4()
    at = (now or datetime.now(tz=UTC)) - inbound_ago
    _run(
        "INSERT INTO wa_conversations (id, brand_id, account_id, wa_id, last_inbound_at, "
        "window_expires_at, unread_count, status, created_at, updated_at) VALUES "
        "(:id, :b, :a, :w, :at, :exp, 1, 'open', now(), now())",
        {
            "id": conv_id,
            "b": brand_id,
            "a": account_id,
            "w": wa_id,
            "at": at,
            "exp": at + timedelta(hours=24),
        },
    )
    _run(
        "INSERT INTO wa_messages (id, conversation_id, brand_id, direction, wamid, kind, body, "
        "delivery_status, created_at) VALUES (:id, :c, :b, 'inbound', :w, 'text', 'hi', "
        "'received', :at)",
        {"id": uuid4(), "c": conv_id, "b": brand_id, "w": f"wamid.S12.{uuid4()}", "at": at},
    )
    return conv_id


def _outbound(
    conv_id: UUID,
    count: int,
    *,
    brand_id: UUID = BRAND_ID,
    ago: timedelta = timedelta(minutes=1),
    now: datetime | None = None,
) -> None:
    at = (now or datetime.now(tz=UTC)) - ago
    for _ in range(count):
        _run(
            "INSERT INTO wa_messages (id, conversation_id, brand_id, direction, wamid, kind, "
            "body, delivery_status, sent_by_user_id, created_at) VALUES (:id, :c, :b, "
            "'outbound', :w, 'text', 'reply', 'sent', :u, :at)",
            {
                "id": uuid4(),
                "c": conv_id,
                "b": brand_id,
                "w": f"wamid.O.{uuid4()}",
                "u": OWNER_USER_ID,
                "at": at,
            },
        )


def _payload(*, wamid: str, referral: dict | None = None) -> dict:
    message = {
        "from": CUSTOMER,
        "id": wamid,
        "timestamp": str(int(datetime.now(tz=UTC).timestamp())),
        "type": "text",
        "text": {"body": "مرحبا"},
    }
    if referral is not None:
        message["referral"] = referral
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_S12",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550001111",
                                "phone_number_id": PNID,
                            },
                            "contacts": [{"profile": {"name": "Nour"}, "wa_id": CUSTOMER}],
                            "messages": [message],
                        },
                    }
                ],
            }
        ],
    }


def _post_webhook(client: TestClient, payload: dict) -> httpx.Response:
    raw = json.dumps(payload).encode("utf-8")
    return client.post(
        WA_WEBHOOK,
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": compute_signature(APP_SECRET, raw),
        },
    )


def _publish_job(
    *,
    status: str,
    external_post_id: str | None,
    updated_ago: timedelta = timedelta(hours=1),
    brand_id: UUID = BRAND_ID,
    now: datetime | None = None,
) -> UUID:
    item_id, rev_id, job_id = uuid4(), uuid4(), uuid4()
    _run(
        "INSERT INTO content_items (id, brand_id, created_by_tenant_id, channel, status, "
        "created_at, updated_at) VALUES (:id, :b, :t, 'facebook_page', 'draft', now(), now())",
        {"id": item_id, "b": brand_id, "t": OWNER_ID},
    )
    _run(
        "INSERT INTO content_revisions (id, item_id, revision_no, body, media_refs, provenance, "
        "created_by_tenant_id, created_at) VALUES (:id, :item, 1, 'post', '[]'::jsonb, "
        "'{}'::jsonb, :t, now())",
        {"id": rev_id, "item": item_id, "t": OWNER_ID},
    )
    _run(
        "INSERT INTO publish_jobs (id, item_id, revision_id, brand_id, channel, target_page_id, "
        "run_at, status, scheduled_by_tenant_id, scheduled_by_user_id, external_post_id, "
        "last_error_class, created_at, updated_at) VALUES (:id, :item, :rev, :b, 'facebook_page', "
        "'1001', now(), :st, :t, :u, :ext, :err, now(), :upd)",
        {
            "id": job_id,
            "item": item_id,
            "rev": rev_id,
            "b": brand_id,
            "st": status,
            "t": OWNER_ID,
            "u": OWNER_USER_ID,
            "ext": external_post_id,
            "err": "permanent" if status == "failed" else None,
            "upd": (now or datetime.now(tz=UTC)) - updated_ago,
        },
    )
    return job_id


async def _pulse_run(now: datetime | None = None) -> list:
    eng = create_async_engine(TEST_DATABASE_URL)
    try:
        async with async_sessionmaker(eng, expire_on_commit=False)() as session, session.begin():
            return await Pulse(PgPulseRepository(session)).run(now)
    finally:
        await eng.dispose()


# =============================================================== 0016 round-trip ===
def test_b_migration_0016_round_trip_on_pg() -> None:
    """``head ⇒ 0015 ⇒ head``: three tables, five columns and eleven named constraints
    vanish and come back; nothing else is dropped; CHECKs refuse bad rows."""
    cfg = alembic_config(TEST_DATABASE_URL)
    command.upgrade(cfg, "head")  # self-heal if an earlier round-trip left the DB behind
    assert _run("SELECT version_num FROM alembic_version", scalar=True) == "0017"
    _run(f"TRUNCATE {', '.join(TABLES)} CASCADE")

    def _tables() -> set[str]:
        return {t for (t,) in _rows("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}

    def _constraints() -> set[str]:
        return {c for (c,) in _rows("SELECT conname FROM pg_constraint")}

    def _columns() -> set[tuple[str, str]]:
        return {
            (t, c)
            for (t, c) in _rows(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public'"
            )
        }

    assert NEW_TABLES <= _tables() and NEW_CONSTRAINTS <= _constraints()
    assert NEW_COLUMNS <= _columns()
    # SPRINT-13: ``head`` is 0017, so going to 0015 also drops the 0017 tables.
    before = _tables() - NEW_TABLES - {"admin_actions", "platform_flags", "brand_exports"}

    command.downgrade(cfg, "0015")
    assert _run("SELECT version_num FROM alembic_version", scalar=True) == "0015"
    assert not NEW_TABLES & _tables()
    assert not NEW_CONSTRAINTS & _constraints()
    assert not NEW_COLUMNS & _columns()
    assert _tables() == before
    assert {"ai_providers", "wa_conversations", "brand_recommendations"} <= _tables()

    command.upgrade(cfg, "head")
    assert _run("SELECT version_num FROM alembic_version", scalar=True) == "0017"
    assert NEW_TABLES <= _tables() and NEW_CONSTRAINTS <= _constraints()
    assert NEW_COLUMNS <= _columns()
    assert {
        n for (n,) in _rows("SELECT indexname FROM pg_indexes WHERE tablename = 'pulse_alerts'")
    } >= {"ix_pulse_alerts_brand_open"}

    # CHECKs and defaults.
    _seed_world()
    assert (
        _run(
            "SELECT column_default FROM information_schema.columns WHERE table_name = "
            "'wa_conversations' AND column_name = 'lead_stage'",
            scalar=True,
        )
        == "'new'::character varying"
    )
    with pytest.raises(IntegrityError, match="ck_pulse_alerts_kind"):
        _run(
            "INSERT INTO pulse_alerts (id, brand_id, kind, severity, payload, created_at) "
            "VALUES (:id, :b, 'made_up', 'warning', '{}'::jsonb, now())",
            {"id": uuid4(), "b": BRAND_ID},
        )
    with pytest.raises(IntegrityError, match="ck_pulse_alerts_severity"):
        _run(
            "INSERT INTO pulse_alerts (id, brand_id, kind, severity, payload, created_at) "
            "VALUES (:id, :b, 'no_publish_7d', 'loud', '{}'::jsonb, now())",
            {"id": uuid4(), "b": BRAND_ID},
        )
    fks = {
        name: (d.decode() if isinstance(d, bytes) else d)
        for (name, d) in _rows(
            "SELECT conname, confdeltype FROM pg_constraint WHERE contype = 'f' AND conname IN "
            "('fk_pulse_alerts_brand_id', 'fk_lead_attributions_brand_id', "
            "'fk_lead_attributions_conversation_id', 'fk_wa_number_caps_account_id')"
        )
    }
    assert set(fks.values()) == {"c"} and len(fks) == 4


# ====================================================================== Pulse ===
def test_b3_pulse_eight_checks_create_alerts_once_and_rearm_after_ack(
    integration_app: FastAPI, integration_settings: Settings, encryption: TokenEncryptionService
) -> None:
    _seed_world()
    account_id = _wa_account(encryption)
    # One deterministic clock for every row *and* for ``Pulse.run``: check (8) counts outbound
    # messages since 00:00 of ``now``'s day, so rows stamped "10 minutes ago" at 00:03 UTC
    # would fall on the previous day (CI flaked exactly that way). Noon keeps every "N hours
    # ago" sample inside the same UTC day; every Pulse query binds this clock, never ``now()``.
    now = datetime.now(tz=UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    # (1) open conversation, unanswered, window closes in 2h
    closing = _conversation(account_id, inbound_ago=timedelta(hours=22), now=now)
    # (2) slow replies: inbound 10h ago, reply 5h ago ⇒ 5h latency > 2h
    slow = _conversation(
        account_id, inbound_ago=timedelta(hours=10), wa_id="201000000002", now=now
    )
    _outbound(slow, 1, ago=timedelta(hours=5), now=now)
    # (3) BRAND2 has jobs but nothing succeeded in 7 days; (4) a failed job for BRAND
    _publish_job(
        status="failed",
        external_post_id=None,
        updated_ago=timedelta(hours=2),
        brand_id=BRAND2_ID,
        now=now,
    )
    failed = _publish_job(
        status="failed", external_post_id=None, updated_ago=timedelta(hours=2), now=now
    )
    _publish_job(
        status="succeeded", external_post_id="999_1", updated_ago=timedelta(hours=1), now=now
    )  # BRAND ok
    # (5) a Meta token expiring in 3 days
    conn_id = uuid4()
    _run(
        "INSERT INTO social_connections (id, brand_id, connected_by_tenant_id, provider, "
        "provider_user_id, encrypted_user_token, granted_scopes, status, token_expires_at, "
        "connected_at, updated_at) VALUES (:id, :b, :t, 'meta', 'u-s12', 'gAAAAA-x', '[]'::jsonb, "
        "'active', now() + interval '3 days', now(), now())",
        {"id": conn_id, "b": BRAND_ID, "t": OWNER_ID},
    )
    # (8) 250 of 300 outbound today on the number (≥ 80 %)
    _outbound(
        closing, 248, ago=timedelta(minutes=10), now=now
    )  # + 1 above on ``slow`` = 249 … add one more
    _outbound(slow, 1, ago=timedelta(minutes=2), now=now)

    created = asyncio.run(_pulse_run(now))
    kinds = sorted(a.kind for a in created)
    assert kinds == [
        "meta_token_expiring",
        "no_publish_7d",
        "publish_failed_24h",  # BRAND2's failed job
        "publish_failed_24h",  # BRAND's failed job
        "wa_daily_cap_80pct",
        "wa_slow_replies",
        "wa_window_closing",
    ], kinds
    by_kind = {a.kind: a for a in created if a.brand_id == BRAND_ID}
    assert {a.brand_id for a in created if a.kind == "publish_failed_24h"} == {BRAND_ID, BRAND2_ID}
    assert by_kind["wa_window_closing"].payload["conversation_id"] == str(closing)
    assert by_kind["wa_window_closing"].severity == "critical"
    assert by_kind["wa_slow_replies"].payload["avg_reply_seconds"] >= 5 * 3600 - 60
    assert [a.brand_id for a in created if a.kind == "no_publish_7d"] == [BRAND2_ID]
    assert by_kind["publish_failed_24h"].payload == {
        "ref": str(failed),
        "job_id": str(failed),
        "error_class": "permanent",
    }
    assert by_kind["meta_token_expiring"].payload["connection_id"] == str(conn_id)
    assert by_kind["wa_daily_cap_80pct"].payload["used"] == 250
    assert by_kind["wa_daily_cap_80pct"].payload["cap"] == 300
    # no customer text anywhere in the payloads (D-069)
    values = [str(v) for a in created for v in a.payload.values()]
    assert "hi" not in values and "reply" not in values  # message bodies
    assert all(CUSTOMER not in v and "Nour" not in v for v in values)  # wa_id / profile name

    # second run ⇒ nothing new (idempotent on open (brand, kind, ref))
    assert asyncio.run(_pulse_run(now)) == []
    assert _run("SELECT count(*) FROM pulse_alerts", scalar=True) == 7

    # routes: list open, ack the window alert (MANAGER), then the check re-arms
    _run(
        "INSERT INTO brand_access_grants (id, brand_id, grantee_tenant_id, role, "
        "granted_by_tenant_id, granted_at) VALUES (:id, :b, :g, 'manager', :t, now())",
        {"id": uuid4(), "b": BRAND_ID, "g": AGENCY_ID, "t": OWNER_ID},
    )
    with TestClient(integration_app) as client:
        res = client.get(
            f"{API}/brands/{BRAND_ID}/pulse/alerts",
            headers=integration_headers(integration_settings, OWNER_ID),
        )
        assert res.status_code == 200, res.text
        assert res.json()["open_count"] == 5  # BRAND2's no_publish is not listed
        window_id = by_kind["wa_window_closing"].id
        res = client.post(
            f"{API}/brands/{BRAND_ID}/pulse/alerts/{window_id}/ack",
            headers=integration_headers(integration_settings, AGENCY_ID),
        )
        assert res.status_code == 200, res.text
        assert res.json()["acknowledged_by_user_id"] == str(USER_BY_TENANT[AGENCY_ID])
        res = client.get(
            f"{API}/brands/{BRAND_ID}/pulse/alerts",
            headers=integration_headers(integration_settings, OWNER_ID),
        )
        assert res.json()["open_count"] == 4
    again = asyncio.run(_pulse_run(now))
    assert [a.kind for a in again] == ["wa_window_closing"]
    assert _run("SELECT count(*) FROM pulse_alerts", scalar=True) == 8


def test_b3_feature_pulse_false_writes_nothing_and_hides_routes(
    integration_settings: Settings,
    encryption: TokenEncryptionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.core import database
    from src.main import create_app, pulse_scheduler_step
    from tests.integration.conftest import reset_db_caches

    _seed_world()
    account_id = _wa_account(encryption)
    _conversation(account_id, inbound_ago=timedelta(hours=22))
    off = integration_settings.model_copy(update={"feature_pulse": False})
    monkeypatch.setattr(database, "get_settings", lambda: off)
    try:
        assert pulse_scheduler_step(off) is None
        app = create_app(off)
        with TestClient(app) as client:
            res = client.get(
                f"{API}/brands/{BRAND_ID}/pulse/alerts", headers=integration_headers(off, OWNER_ID)
            )
            assert res.status_code == 404
    finally:
        reset_db_caches()
    assert _run("SELECT count(*) FROM pulse_alerts", scalar=True) == 0


# ================================================================ caps (D-082) ===
def test_b4_daily_cap_301st_reply_is_429_kill_switch_423_webhook_unaffected(
    integration_app: FastAPI,
    integration_settings: Settings,
    encryption: TokenEncryptionService,
    graph_mock: respx.MockRouter,
) -> None:
    _seed_world()
    account_id = _wa_account(encryption)
    conv_id = _conversation(account_id)
    _outbound(conv_id, 300)  # the default cap is exhausted
    graph_mock.post(f"/{PNID}/messages").mock(
        return_value=httpx.Response(
            200, json={"messaging_product": "whatsapp", "messages": [{"id": "wamid.OUT"}]}
        )
    )
    headers = integration_headers(integration_settings, OWNER_ID)
    with TestClient(integration_app) as client:
        res = client.post(
            f"{WA_BASE}/conversations/{conv_id}/messages", json={"body": "301"}, headers=headers
        )
        assert res.status_code == 429, res.text
        detail = res.json()["detail"]
        assert detail["error"] == "wa_daily_cap_reached" and detail["cap"] == 300
        assert detail["used"] == 300 and 1 <= detail["resets_in_seconds"] <= 86_400
        assert res.headers["Retry-After"] == str(detail["resets_in_seconds"])
        # the webhook keeps accepting inbound traffic
        res = _post_webhook(client, _payload(wamid=f"wamid.IN.{uuid4()}"))
        assert res.status_code == 200 and res.json()["inbound"] == 1
        # raise the cap via the admin route (platform admin), then the send goes through
        admin = integration_settings.model_copy(
            update={"platform_admin_user_ids": [OWNER_USER_ID]}
        )
        from src.core.config import get_settings

        integration_app.dependency_overrides[get_settings] = lambda: admin
        res = client.patch(
            f"{API}/admin/entitlements/wa-caps/{account_id}",
            json={"daily_reply_cap": 500},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        assert res.json()["used_today"] == 300 and res.json()["daily_reply_cap"] == 500
        res = client.post(
            f"{WA_BASE}/conversations/{conv_id}/messages", json={"body": "ok now"}, headers=headers
        )
        assert res.status_code == 201, res.text
        # kill switch ⇒ 423, no network call
        calls_before = len(graph_mock.calls)
        res = client.patch(
            f"{API}/admin/entitlements/wa-caps/{account_id}",
            json={"kill_switch": True},
            headers=headers,
        )
        assert res.status_code == 200 and res.json()["kill_switch"] is True
        res = client.post(
            f"{WA_BASE}/conversations/{conv_id}/messages",
            json={"body": "blocked"},
            headers=headers,
        )
        assert res.status_code == 423 and res.json()["detail"]["error"] == "wa_number_disabled"
        assert len(graph_mock.calls) == calls_before
        integration_app.dependency_overrides.pop(get_settings, None)
    assert _rows("SELECT daily_reply_cap, kill_switch FROM wa_number_caps") == [(500, True)]
    assert (
        _run(
            "SELECT count(*) FROM wa_messages WHERE direction = 'outbound' "
            "AND delivery_status = 'sent'",
            scalar=True,
        )
        == 301
    )


# ====================================================== lead loop (D-079) on PG ===
def test_b2_referral_matching_succeeded_job_creates_measured_attribution_once(
    integration_app: FastAPI, integration_settings: Settings, encryption: TokenEncryptionService
) -> None:
    _seed_world()
    _wa_account(encryption)
    job_id = _publish_job(status="succeeded", external_post_id="123_456")
    _publish_job(status="failed", external_post_id="123_789")  # failed ⇒ never matches
    referral = {"source_id": "123_456", "source_type": "post", "source_url": "https://fb.me/p"}
    with TestClient(integration_app) as client:
        first = _post_webhook(client, _payload(wamid=f"wamid.R.{uuid4()}", referral=referral))
        assert first.status_code == 200, first.text
        assert len(first.json()["attributed"]) == 1
        again = _post_webhook(client, _payload(wamid=f"wamid.R.{uuid4()}", referral=referral))
        assert again.json()["attributed"] == []  # same conversation + job ⇒ unique, no new row
        none = _post_webhook(
            client,
            _payload(wamid=f"wamid.R.{uuid4()}", referral={**referral, "source_id": "123_789"}),
        )
        assert none.json()["attributed"] == []
        # set the stage to lost with a code (OWNER) ⇒ owner_input row with a code only
        conv_id = first.json()["attributed"][0]
        res = client.patch(
            f"{WA_BASE}/conversations/{conv_id}/lead-stage",
            json={"lead_stage": "lost", "objection_code": "price"},
            headers=integration_headers(integration_settings, OWNER_ID),
        )
        assert res.status_code == 200, res.text
    rows = _rows(
        "SELECT grade, source_kind, source_ref, signals::text FROM lead_attributions "
        "ORDER BY grade"
    )
    assert rows == [
        ("measured", "publish_job", str(job_id), '{"referral_source_type": "post"}'),
        (
            "owner_input",
            "lead_stage",
            f"lost:{datetime.now(tz=UTC).date().isoformat()}",
            '{"objection_code": "price"}',
        ),
    ]
    assert _rows("SELECT lead_stage, lead_stage_set_by_user_id FROM wa_conversations") == [
        ("lost", OWNER_USER_ID)
    ]
    with pytest.raises(IntegrityError, match="ck_lead_attributions_grade"):
        _run(
            "INSERT INTO lead_attributions (id, brand_id, conversation_id, grade, source_kind, "
            "source_ref, signals, created_at) SELECT :id, brand_id, conversation_id, 'guessed', "
            "'x', 'y', '{}'::jsonb, now() FROM lead_attributions LIMIT 1",
            {"id": uuid4()},
        )
