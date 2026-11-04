"""SPRINT-13 Part C on PostgreSQL (D-081) — export round-trip (sha256 + zero secrets), brand
delete (204, 404 afterwards, ``ai_cost_log.brand_id`` ⇒ NULL, one ``brand_deleted`` audit row
keyed by ``sha256(brand_id)``), 422 ``confirmation_mismatch`` and the retention sweep
(dry-run deletes nothing; real run deletes only rows older than the cutoff).
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from src.core.security import TokenEncryptionService
from src.modules.lifecycle.infrastructure.postgres_repository import PgLifecycleRepository
from src.modules.lifecycle.service import LifecycleService, brand_hash
from src.modules.ops.infrastructure.postgres_repository import PgOpsRepository
from src.modules.ops.service import OpsService
from tests.conftest import AGENCY_ID, BRAND2_ID, BRAND_ID, OWNER_ID, OWNER_USER_ID
from tests.integration.conftest import TEST_DATABASE_URL, integration_headers
from tests.integration.test_sprint12_b_pg import (
    _conversation,
    _outbound,
    _rows,
    _run,
    _seed_world,
    _wa_account,
)

pytestmark = pytest.mark.integration

API = "/api/v1"
SECRET_FRAGMENTS = (b"access_token", b"refresh_token", b"api_key", b"gAAAA", b"consent")


def _cost_row(brand_id: UUID) -> UUID:
    row_id = uuid4()
    _run(
        "INSERT INTO ai_cost_log (id, brand_id, purpose, provider, model, credential_mode, "
        "latency_ms, prompt_version, outcome, created_at) VALUES (:id, :b, 'brand_diagnosis', "
        "'fake', 'fake-1', 'platform', 10, 'brand_diagnosis_v1', 'ok', now())",
        {"id": row_id, "b": brand_id},
    )
    return row_id


def _actions(action: str) -> list[tuple]:
    return _rows(
        "SELECT target_kind, target_id, actor_user_id, after FROM admin_actions "
        "WHERE action = :a ORDER BY at",
        {"a": action},
    )


def _sweep(*, dry_run: bool, now: datetime) -> dict[str, int]:
    async def _go() -> dict[str, int]:
        eng = create_async_engine(TEST_DATABASE_URL)
        try:
            async with async_sessionmaker(eng, expire_on_commit=False)() as session:
                service = LifecycleService(
                    PgLifecycleRepository(session),
                    audit=OpsService(PgOpsRepository(session)).record,
                )
                report = await service.retention_sweep(
                    now=now, months=12, months_agency=24, export_hours=24, dry_run=dry_run
                )
                await session.commit()
                return report.counts
        finally:
            await eng.dispose()

    return asyncio.run(_go())


# ------------------------------------------------------------------ C-1 ---
def test_c1_export_round_trip_sha256_and_zero_secrets(
    integration_app: FastAPI, integration_encryption: TokenEncryptionService
) -> None:
    _seed_world()
    account_id = _wa_account(integration_encryption)
    conv_id = _conversation(account_id)
    _outbound(conv_id, 2)
    settings = integration_app.state.settings
    h = integration_headers(settings, OWNER_ID)
    with TestClient(integration_app) as client:
        res = client.post(f"{API}/brands/{BRAND_ID}/export", headers=h)
        assert res.status_code == 202, res.text
        body = res.json()
        export_id = body["export_id"]
        stored = _rows(
            "SELECT status, sha256, byte_size, requested_by FROM brand_exports WHERE id = :id",
            {"id": UUID(export_id)},
        )
        assert stored == [("ready", body["sha256"], body["byte_size"], OWNER_USER_ID)]

        dl = client.get(f"{API}/brands/{BRAND_ID}/export/{export_id}", headers=h)
        assert dl.status_code == 200 and dl.headers["content-type"] == "application/zip"
        assert hashlib.sha256(dl.content).hexdigest() == body["sha256"]
        assert dl.headers["x-export-sha256"] == body["sha256"]
        with zipfile.ZipFile(io.BytesIO(dl.content)) as zf:
            manifest = json.loads(zf.read("manifest.json"))
            files = {name: zf.read(name) for name in zf.namelist()}
        assert manifest["brand_hash"] == brand_hash(BRAND_ID)
        assert manifest["counts"]["brand"] == 1
        assert manifest["counts"]["conversations"] == 1
        assert manifest["counts"]["messages"] == 3  # 1 inbound + 2 outbound
        for name, data in files.items():
            if name != "manifest.json":
                assert manifest["sha256"][name[:-5]] == hashlib.sha256(data).hexdigest()
        blob = b"".join(files.values()).lower()
        for fragment in SECRET_FRAGMENTS:
            assert fragment.lower() not in blob, fragment  # §3-12: grep ⇒ 0
        # the brand row itself carries no learning_consent / referral columns
        brand_row = json.loads(files["brand.json"])[0]
        assert "learning_consent" not in brand_row and "slug" in brand_row

        # the other brand's OWNER view (same tenant) is still isolated by brand id
        other = client.get(f"{API}/brands/{BRAND2_ID}/export/{export_id}", headers=h)
        assert other.status_code == 404
        assert other.json()["detail"]["error"] == "export_not_found"
        # a MANAGER / stranger never reaches the export
        res = client.post(
            f"{API}/brands/{BRAND_ID}/export", headers=integration_headers(settings, AGENCY_ID)
        )
        assert res.status_code == 404


# ------------------------------------------------------------------ C-2 ---
def test_c2_delete_brand_cascades_nulls_cost_log_and_audits_hash(
    integration_app: FastAPI, integration_encryption: TokenEncryptionService
) -> None:
    _seed_world()
    account_id = _wa_account(integration_encryption)
    conv_id = _conversation(account_id)
    _outbound(conv_id, 1)
    cost_id = _cost_row(BRAND_ID)
    keep_cost_id = _cost_row(BRAND2_ID)
    settings = integration_app.state.settings
    h = integration_headers(settings, OWNER_ID)
    with TestClient(integration_app) as client:
        bad = client.request(
            "DELETE", f"{API}/brands/{BRAND_ID}", json={"confirm": "nile"}, headers=h
        )
        assert bad.status_code == 422
        assert bad.json()["detail"]["error"] == "confirmation_mismatch"
        assert _rows("SELECT 1 FROM brands WHERE id = :b", {"b": BRAND_ID}) == [(1,)]
        assert _actions("brand_deleted") == []

        res = client.request(
            "DELETE", f"{API}/brands/{BRAND_ID}", json={"confirm": "nile-coffee"}, headers=h
        )
        assert res.status_code == 204, res.text
        assert client.get(f"{API}/brands/{BRAND_ID}", headers=h).status_code == 404
        assert client.get(f"{API}/brands/{BRAND2_ID}", headers=h).status_code == 200

    assert _rows("SELECT 1 FROM brands WHERE id = :b", {"b": BRAND_ID}) == []
    counts = _rows(
        "SELECT (SELECT count(*) FROM whatsapp_accounts WHERE brand_id = :b), "
        "(SELECT count(*) FROM wa_conversations WHERE brand_id = :b), "
        "(SELECT count(*) FROM wa_messages WHERE brand_id = :b)",
        {"b": BRAND_ID},
    )
    assert counts == [(0, 0, 0)]
    # ai_cost_log is retained for finance — brand_id ⇒ NULL (FK ON DELETE SET NULL, 0013)
    assert _rows("SELECT brand_id FROM ai_cost_log WHERE id = :id", {"id": cost_id}) == [(None,)]
    assert _rows("SELECT brand_id FROM ai_cost_log WHERE id = :id", {"id": keep_cost_id}) == [
        (BRAND2_ID,)
    ]
    rows = _actions("brand_deleted")
    assert len(rows) == 1
    kind, target, actor, after = rows[0]
    expected = hashlib.sha256(str(BRAND_ID).encode()).hexdigest()
    assert (kind, target, actor) == ("brand", expected, OWNER_USER_ID)
    # the real app has an outbound client ⇒ unsubscribe ran over zero pages ⇒ "ok"
    assert after == {"brand_hash": expected, "unsubscribe": "ok"}
    assert str(BRAND_ID) not in json.dumps(after)


# ------------------------------------------------------------------ C-3 ---
def test_c3_retention_dry_run_deletes_nothing_and_real_run_only_old_rows(
    integration_app: FastAPI, integration_encryption: TokenEncryptionService
) -> None:
    _seed_world()
    account_id = _wa_account(integration_encryption)
    now = datetime(2026, 11, 3, 12, tzinfo=UTC)
    fresh_conv = _conversation(account_id, inbound_ago=timedelta(days=1), now=now)
    _outbound(fresh_conv, 2, ago=timedelta(days=1), now=now)
    old_conv = _conversation(
        account_id, inbound_ago=timedelta(days=400), now=now, wa_id="201234567001"
    )
    _outbound(old_conv, 3, ago=timedelta(days=400), now=now)
    _run(
        "UPDATE wa_conversations SET updated_at = :at WHERE id = :c",
        {"at": now - timedelta(days=400), "c": old_conv},
    )
    _run(
        "INSERT INTO brand_exports (id, brand_id, status, requested_by, created_at) VALUES "
        "(:id, :b, 'ready', :u, :at)",
        {"id": uuid4(), "b": BRAND_ID, "u": OWNER_USER_ID, "at": now - timedelta(hours=30)},
    )
    cost_id = _cost_row(BRAND_ID)
    _run(
        "UPDATE ai_cost_log SET created_at = :at WHERE id = :id",
        {"at": now - timedelta(days=900), "id": cost_id},
    )
    before_msgs = _run("SELECT count(*) FROM wa_messages", scalar=True)
    assert before_msgs == 7  # 2 inbound + 5 outbound

    counts = _sweep(dry_run=True, now=now)
    assert counts["wa_messages"] == 4 and counts["brand_exports"] == 1
    assert counts["wa_conversations"] == 1
    assert _run("SELECT count(*) FROM wa_messages", scalar=True) == 7
    assert _run("SELECT count(*) FROM brand_exports", scalar=True) == 1
    rows = _actions("retention_sweep")
    assert len(rows) == 1 and rows[0][:3] == ("platform", "retention", None)
    assert rows[0][3]["dry_run"] is True and rows[0][3]["counts"]["wa_messages"] == 4

    counts = _sweep(dry_run=False, now=now)
    assert counts["wa_messages"] == 4 and counts["wa_conversations"] == 1
    assert counts["brand_exports"] == 1
    assert _run("SELECT count(*) FROM wa_messages", scalar=True) == 3  # fresh conversation only
    assert _run("SELECT count(*) FROM wa_conversations", scalar=True) == 1
    assert _run("SELECT count(*) FROM brand_exports", scalar=True) == 0
    # never touched: ai_cost_log / admin_actions
    assert _rows("SELECT 1 FROM ai_cost_log WHERE id = :id", {"id": cost_id}) == [(1,)]
    assert len(_actions("retention_sweep")) == 2

    # agency plan ⇒ 24 months: a 400-day-old row survives the sweep
    _run(
        "INSERT INTO brand_entitlements (brand_id, plan, limits, active_until, is_active, "
        "updated_at) VALUES (:b, 'agency', '{}'::jsonb, :until, true, now()) "
        "ON CONFLICT (brand_id) DO UPDATE SET plan = 'agency', is_active = true, "
        "active_until = EXCLUDED.active_until",
        {"b": BRAND_ID, "until": now + timedelta(days=30)},
    )
    agency_conv = _conversation(
        account_id, inbound_ago=timedelta(days=400), now=now, wa_id="201234567002"
    )
    _outbound(agency_conv, 1, ago=timedelta(days=400), now=now)
    counts = _sweep(dry_run=False, now=now)
    assert counts["wa_messages"] == 0 and counts["wa_conversations"] == 0
