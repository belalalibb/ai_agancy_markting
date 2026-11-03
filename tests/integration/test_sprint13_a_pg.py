"""SPRINT-13 Part A on PostgreSQL — migration 0017 round-trip, ``admin_actions`` written in
the **same transaction** as the act (failed act ⇒ 0 rows), ``brands.suspended_at`` ⇒ 403 on
writes / 200 + ``suspended: true`` on reads, ``platform_readonly`` ⇒ 503 + ``Retry-After``
with ``/auth/*`` and ``/admin/*`` exempt, and the dead-letter requeue CAS (D-055 event)."""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from alembic import command
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from src.core.config import get_settings
from src.modules.ops.service import PLATFORM_FLAGS
from tests.conftest import (
    AGENCY_ID,
    BRAND_ID,
    OWNER_ID,
    OWNER_USER_ID,
    TEST_PASSWORD,
    USER_BY_TENANT,
)
from tests.integration.conftest import (
    TABLES,
    TEST_DATABASE_URL,
    alembic_config,
    integration_headers,
    seed_tenancy,
)

pytestmark = pytest.mark.integration

API = "/api/v1"
ADMIN = f"{API}/admin"
NEW_TABLES = {"admin_actions", "platform_flags", "brand_exports"}
NEW_CONSTRAINTS = {
    "fk_brand_exports_brand_id",
    "ck_brand_exports_status",
    "ck_brand_profiles_sector",
}
NEW_COLUMNS = {
    ("brands", "suspended_at"),
    ("wa_messages", "billable"),
    ("wa_messages", "pricing_category"),
    ("whatsapp_accounts", "quality_rating"),
    ("whatsapp_accounts", "messaging_limit"),
    ("brand_profiles", "sector"),
}
NEW_INDEXES = {"ix_admin_actions_target_at", "ix_brand_exports_brand_created"}
A_TABLES = ("admin_actions", "platform_flags", *TABLES)


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
                await conn.execute(text(f"TRUNCATE {', '.join(A_TABLES)} CASCADE"))
            async with async_sessionmaker(eng, expire_on_commit=False)() as session:
                await seed_tenancy(session)
        finally:
            await eng.dispose()

    asyncio.run(_go())


def _actions(**where: object) -> list[tuple]:
    clause = " AND ".join(f"{k} = :{k}" for k in where) or "TRUE"
    return _rows(
        "SELECT action, target_kind, target_id, actor_user_id, before, after "  # noqa: S608
        f"FROM admin_actions WHERE {clause} ORDER BY at",
        where,
    )


def _failed_job() -> tuple[UUID, UUID, UUID]:
    item_id, rev_id, job_id = uuid4(), uuid4(), uuid4()
    _run(
        "INSERT INTO content_items (id, brand_id, created_by_tenant_id, channel, status) "
        "VALUES (:id, :b, :t, 'facebook_page', 'publish_failed')",
        {"id": item_id, "b": BRAND_ID, "t": OWNER_ID},
    )
    _run(
        "INSERT INTO content_revisions (id, item_id, revision_no, body, provenance, "
        "created_by_tenant_id) VALUES (:id, :item, 1, 'x', CAST(:prov AS JSONB), :t)",
        {"id": rev_id, "item": item_id, "prov": '{"kind": "human"}', "t": OWNER_ID},
    )
    _run(
        "INSERT INTO publish_jobs (id, item_id, revision_id, brand_id, channel, target_page_id, "
        "run_at, status, attempt_count, max_attempts, last_error_class, scheduled_by_tenant_id, "
        "scheduled_by_user_id, created_at, updated_at) VALUES (:id, :item, :rev, :b, "
        "'facebook_page', '1001', now(), 'failed', 3, 3, 'transient', :t, :u, now(), now())",
        {
            "id": job_id,
            "item": item_id,
            "rev": rev_id,
            "b": BRAND_ID,
            "t": OWNER_ID,
            "u": USER_BY_TENANT[OWNER_ID],
        },
    )
    return job_id, item_id, rev_id


@pytest.fixture
def admin_app(integration_app: FastAPI) -> FastAPI:
    """Seeded world + OWNER as platform admin + a cold flag cache."""
    _seed_world()
    PLATFORM_FLAGS.invalidate()
    settings = integration_app.state.settings
    admin = settings.model_copy(update={"platform_admin_user_ids": [OWNER_USER_ID]})
    integration_app.dependency_overrides[get_settings] = lambda: admin
    integration_app.state.admin_settings = admin
    yield integration_app
    PLATFORM_FLAGS.invalidate()


# ------------------------------------------------------------------ 0017 ---------
def test_a_migration_0017_round_trip_on_pg() -> None:
    """``head ⇒ 0016 ⇒ head``: three tables, six columns, named constraints and indexes
    vanish and come back; nothing else is dropped; the widened CHECKs accept the new codes."""
    cfg = alembic_config(TEST_DATABASE_URL)
    command.upgrade(cfg, "head")  # self-heal if an earlier round-trip left the DB behind
    assert _run("SELECT version_num FROM alembic_version", scalar=True) == "0017"
    _run(f"TRUNCATE {', '.join(A_TABLES)} CASCADE")

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

    def _indexes() -> set[str]:
        return {
            i for (i,) in _rows("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
        }

    assert NEW_TABLES <= _tables() and NEW_CONSTRAINTS <= _constraints()
    assert NEW_COLUMNS <= _columns() and NEW_INDEXES <= _indexes()
    before = _tables() - NEW_TABLES

    command.downgrade(cfg, "0016")
    assert _run("SELECT version_num FROM alembic_version", scalar=True) == "0016"
    assert not NEW_TABLES & _tables() and not NEW_CONSTRAINTS & _constraints()
    assert not NEW_COLUMNS & _columns() and not NEW_INDEXES & _indexes()
    assert _tables() == before
    assert {"pulse_alerts", "wa_number_caps", "approval_events"} <= _tables()

    command.upgrade(cfg, "head")
    assert _run("SELECT version_num FROM alembic_version", scalar=True) == "0017"
    assert NEW_TABLES <= _tables() and NEW_CONSTRAINTS <= _constraints()
    assert NEW_COLUMNS <= _columns() and NEW_INDEXES <= _indexes()

    _seed_world()
    # Widened CHECKs: the new pulse kind and approval action are accepted, junk is not.
    _run(
        "INSERT INTO pulse_alerts (id, brand_id, kind, severity, payload, created_at) VALUES "
        "(:id, :b, 'wa_monthly_service_80pct', 'warning', CAST('{}' AS JSONB), now())",
        {"id": uuid4(), "b": BRAND_ID},
    )
    with pytest.raises(IntegrityError, match="ck_pulse_alerts_kind"):
        _run(
            "INSERT INTO pulse_alerts (id, brand_id, kind, severity, payload, created_at) VALUES "
            "(:id, :b, 'nope', 'warning', CAST('{}' AS JSONB), now())",
            {"id": uuid4(), "b": BRAND_ID},
        )
    # ``seed_tenancy`` has no profile row — insert one so the CHECK is actually exercised.
    _run(
        "INSERT INTO brand_profiles (brand_id, sector, updated_at) VALUES "
        "(:b, 'restaurants', now())",
        {"b": BRAND_ID},
    )
    with pytest.raises(IntegrityError, match="ck_brand_profiles_sector"):
        _run("UPDATE brand_profiles SET sector = 'hotels' WHERE brand_id = :b", {"b": BRAND_ID})
    with pytest.raises(IntegrityError, match="ck_brand_exports_status"):
        _run(
            "INSERT INTO brand_exports (id, brand_id, status, requested_by, created_at) VALUES "
            "(:id, :b, 'nope', :u, now())",
            {"id": uuid4(), "b": BRAND_ID, "u": OWNER_USER_ID},
        )
    assert (
        _run(
            "SELECT column_default FROM information_schema.columns WHERE table_name = "
            "'admin_actions' AND column_name = 'before'",
            scalar=True,
        )
        == "'{}'::jsonb"
    )


# ------------------------------------------------------------------ A-1 ----------
def test_a1_admin_action_commits_with_the_act_and_never_alone(admin_app: FastAPI) -> None:
    """``POST /admin/entitlements/activate`` ⇒ the entitlement **and** exactly one
    ``admin_actions`` row (D-055); a refused act (422) ⇒ 0 rows; the non-admin ⇒ 404 (§3-6)."""
    settings = admin_app.state.admin_settings
    h = integration_headers(settings, OWNER_ID)
    with TestClient(admin_app) as client:
        res = client.post(
            f"{ADMIN}/entitlements/activate",
            json={"brand_id": str(BRAND_ID), "plan": "growth", "months": 2},
            headers=h,
        )
        assert res.status_code == 200, res.text
        rows = _actions(action="entitlement_activated")
        assert len(rows) == 1
        action, kind, target, actor, before, after = rows[0]
        assert (kind, target, actor) == ("brand", str(BRAND_ID), OWNER_USER_ID)
        assert after == {"plan": "growth", "months": 2} and before == {}
        res = client.post(
            f"{ADMIN}/entitlements/activate",
            json={"brand_id": str(BRAND_ID), "plan": "gold", "months": 1},
            headers=h,
        )
        assert res.status_code == 422
        assert len(_actions()) == 1  # a refused act leaves no row
        res = client.get(f"{ADMIN}/actions", params={"target_kind": "brand"}, headers=h)
        assert res.status_code == 200 and [a["action"] for a in res.json()] == [
            "entitlement_activated"
        ]
        assert "before" in res.json()[0] and res.json()[0]["actor_user_id"] == str(OWNER_USER_ID)
        for path in (f"{ADMIN}/actions", f"{ADMIN}/flags", f"{ADMIN}/publishing/dead-letter"):
            res = client.get(path, headers=integration_headers(settings, AGENCY_ID))
            assert res.status_code == 404 and res.json()["detail"]["error"] == "not_found"


# ------------------------------------------------------------------ A-2 ----------
def test_a2_suspended_brand_refuses_writes_and_keeps_reads(admin_app: FastAPI) -> None:
    settings = admin_app.state.admin_settings
    h = integration_headers(settings, OWNER_ID)
    body = {"channel": "facebook_page", "body": "hello", "target_page_id": "p1"}
    with TestClient(admin_app) as client:
        assert (
            client.post(f"{API}/brands/{BRAND_ID}/content/items", json=body, headers=h).status_code
            == 201
        )
        res = client.post(f"{ADMIN}/brands/{BRAND_ID}/suspend", headers=h)
        assert res.status_code == 200 and res.json()["suspended"] is True
        assert _run(
            "SELECT suspended_at IS NOT NULL FROM brands WHERE id = :b",
            {"b": BRAND_ID},
            scalar=True,
        )
        res = client.post(f"{API}/brands/{BRAND_ID}/content/items", json=body, headers=h)
        assert res.status_code == 403 and res.json()["detail"]["error"] == "brand_suspended"
        res = client.get(f"{API}/brands/{BRAND_ID}", headers=h)
        assert res.status_code == 200 and res.json()["suspended"] is True  # §3-9
        assert client.get(f"{API}/brands/{BRAND_ID}/content/items", headers=h).status_code == 200
        res = client.post(f"{ADMIN}/brands/{BRAND_ID}/unsuspend", headers=h)
        assert res.status_code == 200 and res.json()["suspended_at"] is None
        assert (
            client.post(f"{API}/brands/{BRAND_ID}/content/items", json=body, headers=h).status_code
            == 201
        )
        assert client.get(f"{API}/brands/{BRAND_ID}", headers=h).json()["suspended"] is False
        assert [r[0] for r in _actions(target_kind="brand")] == [
            "brand_suspended",
            "brand_unsuspended",
        ]
        assert client.post(f"{ADMIN}/brands/{uuid4()}/suspend", headers=h).status_code == 404


def test_a2_platform_readonly_503_with_auth_and_admin_exempt(admin_app: FastAPI) -> None:
    settings = admin_app.state.admin_settings
    h = integration_headers(settings, OWNER_ID)
    body = {"channel": "facebook_page", "body": "hello", "target_page_id": "p1"}
    with TestClient(admin_app) as client:
        flags = {f["key"]: f for f in client.get(f"{ADMIN}/flags", headers=h).json()}
        assert flags["platform_readonly"]["source"] == "settings"
        res = client.put(f"{ADMIN}/flags/platform_readonly", json={"value": True}, headers=h)
        assert res.status_code == 200 and res.json()["source"] == "db"
        assert (
            _run("SELECT value FROM platform_flags WHERE key = 'platform_readonly'", scalar=True)
            is True
        )
        res = client.post(f"{API}/brands/{BRAND_ID}/content/items", json=body, headers=h)
        assert res.status_code == 503 and res.json()["detail"]["error"] == "platform_readonly"
        assert res.headers["Retry-After"] == "300"
        assert client.get(f"{API}/brands/{BRAND_ID}/content/items", headers=h).status_code == 200
        res = client.post(
            f"{API}/auth/login",
            json={"email": "nile-owner@example.com", "password": TEST_PASSWORD},
        )
        assert res.status_code in (200, 401)  # /auth/* exempt — never 503
        res = client.put(f"{ADMIN}/flags/platform_readonly", json={"value": False}, headers=h)
        assert res.status_code == 200  # /admin/* exempt — the switch can be lifted
        assert (
            client.post(f"{API}/brands/{BRAND_ID}/content/items", json=body, headers=h).status_code
            == 201
        )
        assert [(r[0], r[4], r[5]) for r in _actions(target_kind="platform_flag")] == [
            ("flag_set", {"value": False, "source": "settings"}, {"value": True}),
            ("flag_set", {"value": True, "source": "db"}, {"value": False}),
        ]


# ------------------------------------------------------------------ A-3 ----------
def test_a3_dead_letter_requeue_cas_and_d055_event(admin_app: FastAPI) -> None:
    settings = admin_app.state.admin_settings
    h = integration_headers(settings, OWNER_ID)
    job_id, item_id, rev_id = _failed_job()
    with TestClient(admin_app) as client:
        res = client.get(f"{ADMIN}/publishing/dead-letter", headers=h)
        assert res.status_code == 200 and [j["job_id"] for j in res.json()] == [str(job_id)]
        assert res.json()[0]["error_class"] == "transient" and res.json()[0]["attempt_count"] == 3
        res = client.post(f"{ADMIN}/publishing/jobs/{job_id}/requeue", headers=h)
        assert res.status_code == 200 and res.json()["attempt_count"] == 0, res.text
        status, attempts, nxt, leased = _rows(
            "SELECT status, attempt_count, next_attempt_at IS NOT NULL, leased_until "
            "FROM publish_jobs WHERE id = :id",
            {"id": job_id},
        )[0]
        assert (status, attempts, nxt, leased) == ("queued", 0, True, None)
        assert (
            _run("SELECT status FROM content_items WHERE id = :id", {"id": item_id}, scalar=True)
            == "scheduled"
        )
        events = _rows(
            "SELECT action, revision_id, actor_tenant_id, actor_user_id FROM approval_events "
            "WHERE item_id = :id",
            {"id": item_id},
        )
        assert events == [("requeued_by_admin", rev_id, OWNER_ID, OWNER_USER_ID)]
        assert [(r[0], r[1]) for r in _actions(target_kind="publish_job")] == [
            ("job_requeued", "publish_job")
        ]
        res = client.post(f"{ADMIN}/publishing/jobs/{job_id}/requeue", headers=h)
        assert res.status_code == 409 and res.json()["detail"]["error"] == "job_not_failed"
        assert (
            client.post(f"{ADMIN}/publishing/jobs/{uuid4()}/requeue", headers=h).status_code == 404
        )
        assert client.get(f"{ADMIN}/publishing/dead-letter", headers=h).json() == []
        assert len(_actions(target_kind="publish_job")) == 1  # the 409 wrote nothing
        res = client.get(f"{ADMIN}/alerts", params={"kind": "meta_token_expiring"}, headers=h)
        assert res.status_code == 200 and res.json() == []
