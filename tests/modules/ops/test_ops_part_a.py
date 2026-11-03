"""SPRINT-13 Part A — ``ops``: admin_actions audit, DB kill switches, dead-letter requeue,
admin alert digest. DB-less: fakes for the admin routes, ``StubSession`` for the Postgres
adapters (statement composition + row mapping), pure functions for the guard."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.core.audit import audit
from src.core.config import Settings, get_settings
from src.core.flags import effective_flag
from src.modules.ops.audit import AdminAction, PgAuditWriter
from src.modules.ops.dead_letter import PgDeadLetterRepository
from src.modules.ops.flags import FLAG_KEYS, FlagCache, PgFlagRepository, effective
from src.modules.ops.guard import (
    READONLY_RESPONSE,
    SUSPENDED_RESPONSE,
    KillSwitchGuard,
    PgFlagSource,
    brand_write_target,
    decide,
    readonly_applies,
)
from src.modules.ops.router import FLAG_CACHE, AuditAdapter

from tests.conftest import BRAND_ID, OWNER_ID, OWNER_USER_ID, STRANGER_ID
from tests.fakes import FakeAuditLog, FakeFlagSource, StubResult, StubSession, compiled

API = "/api/v1"
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def admin(app: FastAPI, settings: Settings) -> FastAPI:
    on = settings.model_copy(update={"platform_admin_user_ids": [OWNER_USER_ID]})
    app.dependency_overrides[get_settings] = lambda: on
    return app


@pytest.fixture
def admin_client(admin: FastAPI) -> TestClient:
    with TestClient(admin) as c:
        yield c


# =============================================================== A-1 audit trail ===
def test_a1_admin_routes_write_exactly_one_audit_row_each(
    admin_client: TestClient, admin: FakeFlagSource, auth_headers, app: FastAPI
) -> None:
    headers = auth_headers(OWNER_ID)
    log: FakeAuditLog = app.state.audit_log
    # suspend ⇒ one row; unsuspend ⇒ one row; flag ⇒ one row
    r = admin_client.post(f"{API}/admin/brands/{BRAND_ID}/suspend", headers=headers)
    assert r.status_code == 200 and r.json()["suspended"] is True
    r = admin_client.post(f"{API}/admin/brands/{BRAND_ID}/unsuspend", headers=headers)
    assert r.status_code == 200 and r.json()["suspended_at"] is None
    r = admin_client.put(
        f"{API}/admin/flags/platform_readonly", json={"value": True}, headers=headers
    )
    assert r.status_code == 200 and r.json() == {
        "key": "platform_readonly",
        "value": True,
        "updated_at": r.json()["updated_at"],
    }
    assert [a.action for a in log.actions] == ["brand_suspended", "brand_unsuspended", "flag_set"]
    assert log.actions[0].before == {"suspended_at": None}
    assert log.actions[0].after["suspended_at"] is not None
    assert log.actions[2].before == {"value": None} and log.actions[2].after == {"value": True}
    # the list route: newest first, filterable
    r = admin_client.get(f"{API}/admin/actions", headers=headers, params={"limit": 2})
    assert [a["action"] for a in r.json()] == ["flag_set", "brand_unsuspended"]
    r = admin_client.get(f"{API}/admin/actions", headers=headers, params={"target_kind": "brand"})
    assert {a["action"] for a in r.json()} == {"brand_suspended", "brand_unsuspended"}
    assert all(a["target_id"] == str(BRAND_ID) for a in r.json())
    assert admin_client.get(f"{API}/admin/actions?limit=0", headers=headers).status_code == 422


def test_a1_failed_act_writes_no_row(admin_client: TestClient, auth_headers, app: FastAPI) -> None:
    headers = auth_headers(OWNER_ID)
    log: FakeAuditLog = app.state.audit_log
    assert (
        admin_client.post(f"{API}/admin/brands/{uuid4()}/suspend", headers=headers).status_code
        == 404
    )
    assert (
        admin_client.put(
            f"{API}/admin/flags/made_up", json={"value": True}, headers=headers
        ).status_code
        == 404
    )
    assert (
        admin_client.post(
            f"{API}/admin/publishing/jobs/{uuid4()}/requeue", headers=headers
        ).status_code
        == 409
    )
    assert log.actions == []


def test_a1_non_admin_gets_403_on_every_ops_route(client: TestClient, auth_headers) -> None:
    headers = auth_headers(STRANGER_ID)
    for method, path in (
        ("GET", "/admin/actions"),
        ("POST", f"/admin/brands/{BRAND_ID}/suspend"),
        ("POST", f"/admin/brands/{BRAND_ID}/unsuspend"),
        ("GET", "/admin/flags"),
        ("PUT", "/admin/flags/platform_readonly"),
        ("GET", "/admin/publishing/dead-letter"),
        ("POST", f"/admin/publishing/jobs/{uuid4()}/requeue"),
        ("GET", "/admin/alerts"),
    ):
        res = client.request(method, f"{API}{path}", headers=headers, json={"value": True})
        assert res.status_code == 403, (method, path, res.text)
        assert res.json()["detail"]["error"] == "platform_admin_required"


def test_a1_catalog_entitlements_and_wa_caps_routes_audit_through_the_port(
    admin_client: TestClient, auth_headers, app: FastAPI
) -> None:
    """The existing admin routes record one row each via ``src.core.audit`` (names/ids only)."""
    headers = auth_headers(OWNER_ID)
    log: FakeAuditLog = app.state.audit_log
    res = admin_client.post(
        f"{API}/admin/entitlements/activate",
        json={"brand_id": str(BRAND_ID), "plan": "starter", "months": 1},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    assert [a.action for a in log.actions] == ["entitlement_activated"]
    assert log.actions[0].after == {"plan": "starter", "months": 1}
    assert log.actions[0].target_id == str(BRAND_ID)
    # a failed activation ⇒ no row
    res = admin_client.post(
        f"{API}/admin/entitlements/activate",
        json={"brand_id": str(uuid4()), "plan": "starter", "months": 1},
        headers=headers,
    )
    assert res.status_code == 404 and len(log.actions) == 1


async def test_a1_audit_helper_is_inert_without_a_port() -> None:
    await audit(None, actor_user_id=None, action="x", target_kind="y", target_id="z")  # no-op
    log = FakeAuditLog()
    adapter = AuditAdapter(log)
    await adapter.record(
        actor_user_id=OWNER_USER_ID, action="flag_set", target_kind="flag", target_id="k"
    )
    assert log.actions[0].before == {} and log.actions[0].after == {}


async def test_a1_pg_audit_writer_statements_and_mapping() -> None:
    from src.modules.ops.audit import AdminActionRow

    row = AdminActionRow(
        id=uuid4(),
        actor_user_id=None,
        action="retention_sweep",
        target_kind="platform",
        target_id="sweep",
        before={},
        after={"deleted": 0},
        at=NOW,
    )
    session = StubSession([StubResult(scalars=[row])])
    writer = PgAuditWriter(session)  # type: ignore[arg-type]
    act = AdminAction(
        actor_user_id=OWNER_USER_ID, action="flag_set", target_kind="flag", target_id="k"
    )
    assert await writer.record(act) is act
    assert session.flush_count == 1 and session.added[0].action == "flag_set"
    out = await writer.list_actions(target_kind="platform", target_id="sweep", limit=999)
    assert out[0].actor_user_id is None and out[0].after == {"deleted": 0}
    sql = compiled(session.executed[0][0])
    assert "admin_actions.target_kind = " in sql and "ORDER BY admin_actions.at DESC" in sql
    assert "LIMIT" in sql  # capped at MAX_LIST_LIMIT
    out = await writer.list_actions(target_kind=None, target_id=None, limit=5)
    assert out == [] and "WHERE" not in compiled(session.executed[1][0])


# ============================================================ A-2 kill switches ===
def test_a2_readonly_503_everywhere_except_auth_and_admin(
    admin_client: TestClient, auth_headers, app: FastAPI
) -> None:
    headers = auth_headers(OWNER_ID)
    source: FakeFlagSource = app.state.flag_source
    source.values["platform_readonly"] = True
    res = admin_client.post(
        f"{API}/brands/{BRAND_ID}/content/items",
        json={"channel": "facebook_page", "body": "x"},
        headers=headers,
    )
    assert res.status_code == 503 and res.headers["retry-after"] == "300"
    assert res.json()["detail"]["error"] == "platform_readonly"
    # reads stay
    assert admin_client.get(f"{API}/brands/{BRAND_ID}", headers=headers).status_code == 200
    # auth is exempt (401 = wrong password, not 503)
    login = admin_client.post(
        f"{API}/auth/login", json={"email": "nile-owner@example.com", "password": "nope"}
    )
    assert login.status_code == 401
    # admin is exempt: the switch can be lifted
    res = admin_client.put(
        f"{API}/admin/flags/platform_readonly", json={"value": False}, headers=headers
    )
    assert res.status_code == 200 and res.json()["value"] is False
    res = admin_client.post(
        f"{API}/brands/{BRAND_ID}/content/items",
        json={"channel": "facebook_page", "body": "x"},
        headers=headers,
    )
    assert res.status_code == 201


def test_a2_suspended_brand_writes_403_reads_200_with_flag(
    admin_client: TestClient, auth_headers, app: FastAPI
) -> None:
    headers = auth_headers(OWNER_ID)
    assert (
        admin_client.post(f"{API}/admin/brands/{BRAND_ID}/suspend", headers=headers).status_code
        == 200
    )
    res = admin_client.post(
        f"{API}/brands/{BRAND_ID}/content/items",
        json={"channel": "facebook_page", "body": "x"},
        headers=headers,
    )
    assert res.status_code == 403 and res.json()["detail"]["error"] == "brand_suspended"
    read = admin_client.get(f"{API}/brands/{BRAND_ID}", headers=headers)
    assert read.status_code == 200 and read.json()["suspended"] is True
    assert read.json()["suspended_at"] is not None
    assert (
        admin_client.post(f"{API}/admin/brands/{BRAND_ID}/unsuspend", headers=headers).status_code
        == 200
    )
    read = admin_client.get(f"{API}/brands/{BRAND_ID}", headers=headers)
    assert read.json()["suspended"] is False and read.json()["suspended_at"] is None
    listed = admin_client.get(f"{API}/admin/flags", headers=headers).json()
    assert listed == [] or all(set(f) == {"key", "value", "updated_at"} for f in listed)


def test_a2_guard_pure_functions() -> None:
    assert readonly_applies("POST", "/api/v1/brands/x/content/items")
    assert not readonly_applies("GET", "/api/v1/brands/x")
    assert not readonly_applies("POST", "/api/v1/auth/login")
    assert not readonly_applies("PUT", "/api/v1/admin/flags/platform_readonly")
    assert not readonly_applies("POST", "/app/login")
    assert not readonly_applies("POST", "/api/v1")
    assert brand_write_target("POST", f"/api/v1/brands/{BRAND_ID}/content/items") == BRAND_ID
    assert brand_write_target("DELETE", f"/api/v1/brands/{BRAND_ID}") is None  # C-2 exempt
    assert brand_write_target("DELETE", f"/api/v1/brands/{BRAND_ID}/grants/{uuid4()}") == BRAND_ID
    assert brand_write_target("GET", f"/api/v1/brands/{BRAND_ID}") is None
    assert brand_write_target("POST", "/api/v1/brands") is None
    assert (
        brand_write_target("POST", "/api/v1/brands/zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz/x") is None
    )
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert effective({}, settings, "feature_pulse") is settings.feature_pulse
    assert effective({"feature_pulse": False}, settings, "feature_pulse") is False
    assert effective({}, settings, "platform_readonly") is False


async def test_a2_decide_and_middleware_paths(settings: Settings) -> None:
    source = FakeFlagSource()
    assert await decide(None, settings, "POST", "/api/v1/brands/x") is None
    assert await decide(source, settings, "POST", f"/api/v1/brands/{BRAND_ID}/x") is None
    source.suspended[BRAND_ID] = NOW
    assert (
        await decide(source, settings, "POST", f"/api/v1/brands/{BRAND_ID}/x")
        is SUSPENDED_RESPONSE
    )
    assert await decide(source, settings, "DELETE", f"/api/v1/brands/{BRAND_ID}") is None
    source.values["platform_readonly"] = True
    assert await decide(source, settings, "POST", "/api/v1/brands") is READONLY_RESPONSE
    assert await decide(source, settings, "POST", "/api/v1/admin/x") is None

    sent: list[dict[str, Any]] = []
    inner: list[str] = []

    async def inner_app(scope: Any, receive: Any, send: Any) -> None:
        inner.append(scope["type"])

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    class _State:
        flag_source = source

    class _App:
        state = _State()

    guard = KillSwitchGuard(inner_app, settings=settings)
    await guard({"type": "lifespan"}, None, send)
    await guard(
        {"type": "http", "method": "GET", "path": "/api/v1/brands", "app": _App()}, None, send
    )
    assert inner == ["lifespan", "http"] and sent == []
    await guard(
        {"type": "http", "method": "POST", "path": "/api/v1/brands", "app": _App()}, None, send
    )
    assert sent[0]["status"] == 503 and (b"retry-after", b"300") in sent[0]["headers"]
    assert b"platform_readonly" in sent[1]["body"]
    await guard({"type": "http", "method": "POST", "path": "/api/v1/brands"}, None, send)  # no app
    assert inner == ["lifespan", "http", "http"]


async def test_a2_flag_cache_ttl_and_pg_source_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = FakeFlagSource()
    repo.values["feature_pulse"] = False
    cache = FlagCache(ttl_seconds=30)
    clock = {"t": 1000.0}
    monkeypatch.setattr("src.modules.ops.flags.time.monotonic", lambda: clock["t"])
    assert await cache.flags(repo) == {"feature_pulse": False}
    repo.values["feature_pulse"] = True
    assert await cache.flags(repo) == {"feature_pulse": False}  # cached
    clock["t"] += 31
    assert await cache.flags(repo) == {"feature_pulse": True}  # expired
    repo.suspended[BRAND_ID] = NOW
    assert await cache.suspended_at(repo, BRAND_ID) == NOW
    del repo.suspended[BRAND_ID]
    assert await cache.suspended_at(repo, BRAND_ID) == NOW  # cached
    cache.invalidate()
    assert await cache.suspended_at(repo, BRAND_ID) is None

    # PgFlagSource: a broken session factory ⇒ {} / None and a 30 s negative cache.
    def boom() -> Any:
        raise RuntimeError("db down")

    source = PgFlagSource(FlagCache(), session_factory=boom)
    monkeypatch.setattr("src.modules.ops.guard.time.monotonic", lambda: clock["t"])
    assert await source.flags() == {} and await source.suspended_at(BRAND_ID) is None
    assert source._degraded()  # noqa: SLF001
    source.invalidate()
    assert not source._degraded()  # noqa: SLF001
    assert await source.suspended_at(BRAND_ID) is None and source._degraded()  # noqa: SLF001
    assert await source.flags() == {}  # short-circuits while degraded


async def test_a2_pg_flag_repository_statements() -> None:
    session = StubSession(
        [
            StubResult(rows=[("platform_readonly", True), ("feature_pulse", False)]),
            StubResult(rows=[("feature_pulse", True, NOW)]),
            StubResult(rows=[(NOW,)]),
            StubResult(rows=[]),
            StubResult(rows=[(BRAND_ID,)]),
            StubResult(rows=[]),
        ]
    )
    repo = PgFlagRepository(session)  # type: ignore[arg-type]
    assert await repo.all_flags() == {"platform_readonly": True, "feature_pulse": False}
    flag = await repo.set_flag("feature_pulse", True, now=NOW)
    assert flag.value is True and flag.updated_at == NOW
    assert "ON CONFLICT (key) DO UPDATE" in str(session.executed[1][0])
    assert await repo.brand_suspended_at(BRAND_ID) == NOW
    assert await repo.brand_suspended_at(BRAND_ID) is False  # unknown brand
    assert await repo.set_brand_suspended(BRAND_ID, NOW) is True
    assert await repo.set_brand_suspended(BRAND_ID, None) is False
    assert session.executed[4][1] == {"brand_id": BRAND_ID, "suspended_at": NOW}
    assert FLAG_KEYS == ("platform_readonly", "feature_pulse", "feature_lead_loop")


async def test_a2_effective_flag_consumer_helper(settings: Settings) -> None:
    class _State:
        flag_source: Any = None

    class _App:
        state = _State()

    app = _App()
    assert await effective_flag(app, settings, "feature_lead_loop") is settings.feature_lead_loop
    src = FakeFlagSource()
    src.values["feature_lead_loop"] = False
    app.state.flag_source = src
    assert await effective_flag(app, settings, "feature_lead_loop") is False

    class _Broken:
        async def flags(self) -> dict[str, bool]:
            raise RuntimeError("x")

    app.state.flag_source = _Broken()
    assert await effective_flag(app, settings, "feature_pulse") is settings.feature_pulse
    assert await effective_flag(None, settings, "platform_readonly") is False


async def test_a2_pulse_step_skips_when_db_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.modules.pulse import scheduler_step

    class _Ctx:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *a: object) -> None:
            return None

    monkeypatch.setattr(scheduler_step, "independent_session", lambda factory: _Ctx())
    ran: list[datetime] = []

    class _Pulse:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        async def run(self, now: datetime) -> list[Any]:
            ran.append(now)
            return []

    monkeypatch.setattr(scheduler_step, "Pulse", _Pulse)
    monkeypatch.setattr(scheduler_step, "PgPulseRepository", lambda s: s)
    enabled = {"v": False}

    async def _enabled() -> bool:
        return enabled["v"]

    step = scheduler_step.pulse_step(
        interval_seconds=900, session_factory=lambda: None, enabled=_enabled
    )
    assert await step(NOW) == 0 and ran == []  # DB flag off ⇒ skipped, last_run untouched
    enabled["v"] = True
    assert await step(NOW) == 0 and ran == [NOW]


def test_a2_lead_loop_reads_db_flag_first(
    admin_client: TestClient, auth_headers, app: FastAPI, settings: Settings
) -> None:
    """``feature_lead_loop=false`` in the DB ⇒ the lead-stage route answers as if the env
    switch were off (the route's own 404/409 contract is unchanged — only the switch moved)."""
    from src.modules.whatsapp.router import get_lead_loop_service

    source: FakeFlagSource = app.state.flag_source
    source.values["feature_lead_loop"] = False
    seen: list[bool] = []

    class _Req:
        def __init__(self, application: FastAPI) -> None:
            self.app = application

    async def probe() -> None:
        svc = await get_lead_loop_service(_Req(app), repo=None, settings=settings, posts=None)  # type: ignore[arg-type]
        seen.append(svc._enabled)  # noqa: SLF001

    import asyncio

    asyncio.run(probe())
    source.values["feature_lead_loop"] = True
    asyncio.run(probe())
    assert seen == [False, True]


# ============================================================== A-3 dead-letter ===
async def test_a3_pg_dead_letter_statements_and_cas() -> None:
    job, item, rev = uuid4(), uuid4(), uuid4()
    row = (job, item, BRAND_ID, "facebook_page", "permanent", 3, 3, NOW)
    session = StubSession(
        [
            StubResult(rows=[row]),  # list_failed
            StubResult(rows=[(*row, rev, NOW)]),  # requeue hit
            StubResult(rows=[]),  # requeue miss (not failed)
            StubResult(rows=[(item,)]),  # reschedule_item CAS hit
            StubResult(),  # event insert
            StubResult(rows=[]),  # reschedule_item CAS miss
        ]
    )
    repo = PgDeadLetterRepository(session)  # type: ignore[arg-type]
    (listed,) = await repo.list_failed(limit=10_000)
    assert listed.job_id == job and listed.error_class == "permanent" and listed.failed_at == NOW
    assert session.executed[0][1] == {"limit": 200}
    got = await repo.requeue(job, now=NOW)
    assert got is not None and got.item_id == item and repo.last_revision_id == rev
    sql = str(session.executed[1][0])
    assert "status = 'queued', attempt_count = 0, next_attempt_at = :now" in sql
    assert "WHERE id = :job_id AND status = 'failed'" in sql
    assert await repo.requeue(job, now=NOW) is None
    assert await repo.reschedule_item(item, rev, now=NOW) is True
    assert "status = 'publish_failed'" in str(session.executed[3][0])
    assert "'requeued_by_admin'" in str(session.executed[4][0])
    assert session.executed[4][1]["revision_id"] == rev
    assert await repo.reschedule_item(item, rev, now=NOW) is False
    assert len(session.executed) == 6  # no event when the CAS missed


def test_a3_requeue_route_409_when_not_failed_and_audits_on_success(
    admin_client: TestClient, auth_headers, app: FastAPI
) -> None:
    from src.modules.ops.dead_letter import DeadLetterJob

    headers = auth_headers(OWNER_ID)
    job_id, item_id = uuid4(), uuid4()
    failed = DeadLetterJob(
        job_id=job_id,
        item_id=item_id,
        brand_id=BRAND_ID,
        channel="facebook_page",
        error_class="permanent",
        attempt_count=3,
        max_attempts=3,
        failed_at=NOW,
    )

    fake = app.state.dead_letters
    fake.jobs[job_id] = failed
    listed = admin_client.get(f"{API}/admin/publishing/dead-letter", headers=headers).json()
    assert [j["job_id"] for j in listed] == [str(job_id)] and listed[0][
        "error_class"
    ] == "permanent"
    res = admin_client.post(f"{API}/admin/publishing/jobs/{job_id}/requeue", headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "queued" and res.json()["item_rescheduled"] is True
    assert fake.rescheduled == [item_id]
    log: FakeAuditLog = app.state.audit_log
    assert [a.action for a in log.actions] == ["requeued_by_admin"]
    assert (
        log.actions[0].after["item_id"] == str(item_id)
        and log.actions[0].after["attempt_count"] == 0
    )
    res = admin_client.post(f"{API}/admin/publishing/jobs/{job_id}/requeue", headers=headers)
    assert res.status_code == 409 and res.json()["detail"]["error"] == "job_not_failed"
    assert len(log.actions) == 1


# =================================================================== A-4 alerts ===
def test_a4_admin_alerts_digest(admin_client: TestClient, auth_headers, app: FastAPI) -> None:
    from src.core.database import get_db_session

    alert_id = uuid4()
    session = StubSession(
        [
            StubResult(
                rows=[
                    (
                        alert_id,
                        BRAND_ID,
                        "meta_token_expiring",
                        "critical",
                        {"ref": "c", "reason": "refresh_failed"},
                        NOW,
                        None,
                    )
                ]
            ),
            StubResult(rows=[]),
        ]
    )

    async def _session():  # noqa: ANN202
        yield session

    app.dependency_overrides[get_db_session] = _session
    headers = auth_headers(OWNER_ID)
    res = admin_client.get(f"{API}/admin/alerts", headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()[0]["payload"]["reason"] == "refresh_failed"
    assert session.executed[0][1] == {"kind": "meta_token_expiring", "limit": 50}
    res = admin_client.get(f"{API}/admin/alerts", headers=headers, params={"kind": "", "limit": 3})
    assert res.json() == [] and session.executed[1][1] == {"kind": None, "limit": 3}


async def test_a4_token_expiring_marks_refresh_failed() -> None:
    from src.modules.pulse.infrastructure.postgres_repository import PgPulseRepository

    conn = uuid4()
    session = StubSession(
        [
            StubResult(
                rows=[
                    (conn, BRAND_ID, NOW + timedelta(days=2), "active"),
                    (conn, BRAND_ID, None, "reauth_required"),
                ]
            )
        ]
    )
    (soon, failed) = await PgPulseRepository(session).token_expiring(NOW, days=7)  # type: ignore[arg-type]
    assert soon.severity == "warning" and soon.payload["reason"] == "expiring"
    assert failed.severity == "critical" and failed.payload["reason"] == "refresh_failed"
    assert failed.payload["token_expires_at"] is None
    assert "OR status = 'reauth_required'" in str(session.executed[0][0])


# ============================================================ migration module ===
def test_0017_migration_module_is_well_formed() -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "alembic" / "versions" / "0017_day2_ops.py"
    spec = importlib.util.spec_from_file_location("m0017", path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    assert module.revision == "0017" and module.down_revision == "0016"
    assert "'requeued_by_admin'" in module.NEW_APPROVAL_ACTIONS
    assert "'wa_monthly_service_80pct'" in module.NEW_PULSE_KINDS
    assert FLAG_CACHE is not None
