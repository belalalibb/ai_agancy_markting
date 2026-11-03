"""SPRINT-12 B-3 — Pulse (SQL-only observer): service dedup/idempotency, the two routes,
the scheduler step and the Postgres repository / entitlements adapter on ``StubSession``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.core.config import Settings, get_settings
from src.core.metrics import counters
from src.modules.entitlements.dependencies import PgPlanCounterUsageReader
from src.modules.entitlements.service import PlanCounterUsageRow, plan_counter_usage
from src.modules.pulse import scheduler_step
from src.modules.pulse.domain import ALERT_KINDS, Alert, CounterUsage, Finding
from src.modules.pulse.infrastructure.postgres_repository import (
    PgPulseRepository,
    PulseAlertRow,
)
from src.modules.pulse.router import get_pulse_repository
from src.modules.pulse.service import Pulse, counter_findings
from src.modules.tenancy.domain.models import GrantRole

from tests.conftest import AGENCY_ID, BRAND2_ID, BRAND_ID, OWNER_ID, STRANGER_ID, grant
from tests.fakes import FakeStore, StubResult, StubSession, compiled

pytestmark = pytest.mark.unit

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
API = "/api/v1"
ALERTS = f"{API}/brands/{BRAND_ID}/pulse/alerts"


class FakePulseRepository:
    """In-memory ``PulseRepository``; the eight checks return scripted findings."""

    def __init__(self) -> None:
        self.alerts: dict[UUID, Alert] = {}
        self.findings: list[Finding] = []
        self.calls: list[str] = []

    async def open_refs(self, kind: str) -> set[tuple[UUID, str]]:
        return {(a.brand_id, a.ref) for a in self.alerts.values() if a.kind == kind and a.is_open}

    async def add_alerts(self, alerts: list[Alert]) -> list[Alert]:
        for alert in alerts:
            self.alerts[alert.id] = alert
        return alerts

    async def list_alerts(self, brand_id: UUID, *, open_only: bool, limit: int) -> list[Alert]:
        rows = [
            a
            for a in self.alerts.values()
            if a.brand_id == brand_id and (not open_only or a.is_open)
        ]
        return sorted(rows, key=lambda a: a.created_at, reverse=True)[:limit]

    async def acknowledge(
        self, brand_id: UUID, alert_id: UUID, *, actor_user_id: UUID, now: datetime
    ) -> Alert | None:
        alert = self.alerts.get(alert_id)
        if alert is None or alert.brand_id != brand_id:
            return None
        if alert.is_open:
            alert = alert.model_copy(
                update={"acknowledged_at": now, "acknowledged_by_user_id": actor_user_id}
            )
            self.alerts[alert_id] = alert
        return alert

    def _check(self, name: str, kind: str) -> list[Finding]:
        self.calls.append(name)
        return [f for f in self.findings if f.kind == kind]

    async def window_closing(self, now: datetime, *, within_seconds: int) -> list[Finding]:
        return self._check("window_closing", "wa_window_closing")

    async def slow_replies(self, now: datetime, *, threshold_seconds: int) -> list[Finding]:
        return self._check("slow_replies", "wa_slow_replies")

    async def no_publish(self, now: datetime, *, days: int) -> list[Finding]:
        return self._check("no_publish", "no_publish_7d")

    async def publish_failed(self, now: datetime, *, hours: int) -> list[Finding]:
        return self._check("publish_failed", "publish_failed_24h")

    async def token_expiring(self, now: datetime, *, days: int) -> list[Finding]:
        return self._check("token_expiring", "meta_token_expiring")

    async def quality_degraded(self, now: datetime) -> list[Finding]:
        return self._check("quality_degraded", "wa_quality_degraded")

    async def daily_cap_near(self, now: datetime, *, ratio: float) -> list[Finding]:
        return self._check("daily_cap_near", "wa_daily_cap_80pct")

    async def monthly_service_near(
        self, now: datetime, *, threshold: int, cap: int
    ) -> list[Finding]:
        return self._check("monthly_service_near", "wa_monthly_service_80pct")


class FakeCounters:
    def __init__(self, rows: list[CounterUsage]) -> None:
        self.rows = rows

    async def usage(self, now: datetime) -> list[CounterUsage]:
        return self.rows


def _finding(kind: str, ref: str, brand_id: UUID = BRAND_ID, severity: str = "warning") -> Finding:
    return Finding(brand_id=brand_id, kind=kind, severity=severity, payload={"ref": ref})  # type: ignore[arg-type]


# ------------------------------------------------------------------------ service ---
async def test_pulse_runs_all_nine_checks_and_is_idempotent_until_ack() -> None:
    counters.reset()
    repo = FakePulseRepository()
    repo.findings = [
        _finding("wa_window_closing", "conv-1", severity="critical"),
        _finding("wa_window_closing", "conv-1"),  # duplicate within one run
        _finding("no_publish_7d", "7d"),
        _finding("no_publish_7d", "7d", brand_id=BRAND2_ID),
    ]
    pulse = Pulse(
        repo,
        counters_reader=FakeCounters(
            [CounterUsage(brand_id=BRAND_ID, counter="monthly_posts", used=9, limit=10)]
        ),
    )
    created = await pulse.run(NOW)
    assert [a.kind for a in created] == [
        "wa_window_closing",
        "no_publish_7d",
        "no_publish_7d",
        "plan_counter_80pct",
    ]
    assert repo.calls == [
        "window_closing",
        "slow_replies",
        "no_publish",
        "publish_failed",
        "token_expiring",
        "quality_degraded",
        "daily_cap_near",
        "monthly_service_near",  # SPRINT-13 B-1 (9)
    ]
    assert counters["pulse_alerts_created"] == 4
    # second run: everything is still open ⇒ nothing new
    assert await pulse.run(NOW + timedelta(minutes=15)) == []
    assert len(repo.alerts) == 4
    # acknowledge the window alert ⇒ the same finding re-raises on the next run
    window = next(a for a in repo.alerts.values() if a.kind == "wa_window_closing")
    assert await repo.acknowledge(BRAND_ID, window.id, actor_user_id=uuid4(), now=NOW) is not None
    again = await pulse.run(NOW + timedelta(minutes=30))
    assert [a.kind for a in again] == ["wa_window_closing"] and len(repo.alerts) == 5
    assert all(a.created_at == NOW + timedelta(minutes=30) for a in again)
    # ``run()`` without ``now`` uses the clock
    assert await Pulse(FakePulseRepository()).run() == []


def test_counter_findings_thresholds() -> None:
    rows = [
        CounterUsage(brand_id=BRAND_ID, counter="monthly_posts", used=7, limit=10),
        CounterUsage(brand_id=BRAND_ID, counter="monthly_wa_conversations", used=8, limit=10),
        CounterUsage(brand_id=BRAND2_ID, counter="monthly_posts", used=10, limit=10),
        CounterUsage(brand_id=BRAND2_ID, counter="monthly_wa_conversations", used=5, limit=0),
    ]
    out = counter_findings(rows)
    assert [(f.brand_id, f.severity, f.payload["ref"]) for f in out] == [
        (BRAND_ID, "warning", "monthly_wa_conversations"),
        (BRAND2_ID, "critical", "monthly_posts"),
    ]
    assert out[0].payload == {
        "ref": "monthly_wa_conversations",
        "counter": "monthly_wa_conversations",
        "used": 8,
        "limit": 10,
    }
    assert len(ALERT_KINDS) == 9 and "plan_counter_80pct" in ALERT_KINDS  # SPRINT-13: +(9)
    alert = out[1].to_alert(NOW)
    assert alert.is_open and alert.ref == "monthly_posts" and alert.created_at == NOW


# ------------------------------------------------------------------------- routes ---
@pytest.fixture
def pulse_repo() -> FakePulseRepository:
    return FakePulseRepository()


@pytest.fixture
def pulse_app(app: FastAPI, store: FakeStore, pulse_repo: FakePulseRepository) -> FastAPI:
    grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    grant(store, BRAND_ID, STRANGER_ID, GrantRole.VIEWER)
    app.dependency_overrides[get_pulse_repository] = lambda: pulse_repo
    return app


def _seed(repo: FakePulseRepository, brand_id: UUID = BRAND_ID, **payload: Any) -> Alert:
    alert = Alert(
        brand_id=brand_id,
        kind="publish_failed_24h",
        severity="warning",
        payload={"ref": str(uuid4()), **payload},
        created_at=NOW,
    )
    repo.alerts[alert.id] = alert
    return alert


def test_list_alerts_roles_open_filter_and_isolation(
    pulse_app: FastAPI, pulse_repo: FakePulseRepository, auth_headers
) -> None:
    mine = _seed(pulse_repo, job_id="j1")
    other = _seed(pulse_repo, brand_id=BRAND2_ID)
    acked = _seed(pulse_repo).model_copy(update={"acknowledged_at": NOW})
    pulse_repo.alerts[acked.id] = acked
    with TestClient(pulse_app) as client:
        res = client.get(ALERTS, headers=auth_headers(STRANGER_ID))  # VIEWER may read
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["open_count"] == 1 and [i["id"] for i in body["items"]] == [str(mine.id)]
        assert body["items"][0]["payload"] == {"ref": mine.ref, "job_id": "j1"}
        res = client.get(ALERTS, params={"open": "false"}, headers=auth_headers(OWNER_ID))
        ids = {i["id"] for i in res.json()["items"]}
        assert ids == {str(mine.id), str(acked.id)} and str(other.id) not in ids
        assert res.json()["open_count"] == 1
        assert (
            client.get(ALERTS, params={"limit": 0}, headers=auth_headers(OWNER_ID)).status_code
            == 422
        )
        # a user without any grant on the brand ⇒ 404 brand_not_found (no existence leak)
        res = client.get(
            f"{API}/brands/{BRAND2_ID}/pulse/alerts", headers=auth_headers(STRANGER_ID)
        )
        assert res.status_code == 404


def test_ack_roles_404_and_idempotent(
    pulse_app: FastAPI, pulse_repo: FakePulseRepository, auth_headers
) -> None:
    alert = _seed(pulse_repo)
    foreign = _seed(pulse_repo, brand_id=BRAND2_ID)
    with TestClient(pulse_app) as client:
        res = client.post(f"{ALERTS}/{alert.id}/ack", headers=auth_headers(STRANGER_ID))
        assert res.status_code == 403, res.text  # VIEWER
        res = client.post(f"{ALERTS}/{alert.id}/ack", headers=auth_headers(AGENCY_ID))
        assert res.status_code == 200, res.text
        assert res.json()["acknowledged_at"] is not None
        assert res.json()["acknowledged_by_user_id"] is not None
        # already acknowledged ⇒ still 200 with the same row (idempotent)
        again = client.post(f"{ALERTS}/{alert.id}/ack", headers=auth_headers(OWNER_ID))
        assert again.status_code == 200 and again.json() == res.json()
        # another brand's alert through my brand's URL ⇒ 404 pulse_alert_not_found
        res = client.post(f"{ALERTS}/{foreign.id}/ack", headers=auth_headers(OWNER_ID))
        assert res.status_code == 404 and res.json()["detail"]["error"] == "pulse_alert_not_found"
        res = client.post(f"{ALERTS}/{uuid4()}/ack", headers=auth_headers(OWNER_ID))
        assert res.status_code == 404
    assert pulse_repo.alerts[foreign.id].is_open


def test_feature_pulse_off_routes_404_and_nothing_mounted(
    pulse_app: FastAPI, pulse_repo: FakePulseRepository, auth_headers, settings: Settings
) -> None:
    alert = _seed(pulse_repo)
    off = settings.model_copy(update={"feature_pulse": False})
    pulse_app.dependency_overrides[get_settings] = lambda: off
    with TestClient(pulse_app) as client:
        res = client.get(ALERTS, headers=auth_headers(OWNER_ID))
        assert res.status_code == 404 and res.json()["detail"]["error"] == "pulse_disabled"
        res = client.post(f"{ALERTS}/{alert.id}/ack", headers=auth_headers(OWNER_ID))
        assert res.status_code == 404
    assert pulse_repo.alerts[alert.id].is_open
    # a fresh app built with FEATURE_PULSE=false has no pulse routes and no step
    from src.main import create_app, pulse_scheduler_step

    paths = create_app(off).openapi()["paths"]
    assert not any("/pulse/" in p for p in paths)
    assert pulse_scheduler_step(off) is None
    assert pulse_scheduler_step(settings) is not None


# ----------------------------------------------------------------- scheduler step ---
async def test_pulse_step_respects_interval_and_uses_independent_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs: list[datetime] = []
    sessions: list[object] = []

    class _Pulse:
        def __init__(self, repo: object, *, counters_reader: object) -> None:
            self.reader = counters_reader

        async def run(self, now: datetime) -> list[Alert]:
            runs.append(now)
            return [object(), object()]  # type: ignore[list-item]

    class _Session:
        pass

    class _Ctx:
        async def __aenter__(self) -> _Session:
            s = _Session()
            sessions.append(s)
            return s

        async def __aexit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(scheduler_step, "Pulse", _Pulse)
    monkeypatch.setattr(scheduler_step, "independent_session", lambda factory: _Ctx())
    seen_readers: list[object] = []

    def factory(session: object) -> object:
        seen_readers.append(session)
        return "reader"

    step = scheduler_step.pulse_step(
        interval_seconds=900,
        counters_factory=factory,
        session_factory=lambda: "sf",  # type: ignore[arg-type,return-value]
    )
    assert await step(NOW) == 2
    assert await step(NOW + timedelta(seconds=899)) == 0  # too early
    assert await step(NOW + timedelta(seconds=900)) == 2
    assert runs == [NOW, NOW + timedelta(seconds=900)] and len(sessions) == 2
    assert seen_readers == sessions  # the reader is built on the same session
    # no counters factory ⇒ check (6) skipped (reader None)
    plain = scheduler_step.pulse_step(interval_seconds=1, session_factory=lambda: "sf")  # type: ignore[arg-type,return-value]
    assert await plain(NOW) == 2


def test_main_scheduler_wires_pulse_as_extra_step(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src import main as main_module

    captured: dict[str, Any] = {}

    async def fake_loop(scheduler: object, poll: float, *, extra_steps: tuple) -> None:  # type: ignore[type-arg]
        captured["steps"] = extra_steps

    class _Task:
        pass

    monkeypatch.setattr(main_module, "scheduler_loop", fake_loop)
    monkeypatch.setattr(main_module, "PostgresScheduler", lambda *a, **k: object())
    monkeypatch.setattr(
        main_module.asyncio, "create_task", lambda coro: (coro.close(), _Task())[1]
    )
    monkeypatch.setattr(main_module, "build_publisher_resolver", lambda s, h: object())
    on = settings.model_copy(update={"scheduler_enabled": True})
    app = main_module.create_app(on)
    app.state.http_client = object()
    assert isinstance(main_module._start_scheduler(app, on), _Task)
    # the coroutine was closed before running; call the loop directly to capture steps
    import asyncio

    asyncio.run(
        fake_loop(
            object(),
            1,
            extra_steps=tuple(
                s
                for s in (
                    main_module.settle_stale_cost_reservations_step(on),
                    main_module.pulse_scheduler_step(on),
                )
                if s is not None
            ),
        )
    )
    assert len(captured["steps"]) == 2
    off = on.model_copy(update={"feature_pulse": False})
    asyncio.run(
        fake_loop(
            object(),
            1,
            extra_steps=tuple(
                s
                for s in (
                    main_module.settle_stale_cost_reservations_step(off),
                    main_module.pulse_scheduler_step(off),
                )
                if s is not None
            ),
        )
    )
    assert len(captured["steps"]) == 1


# ------------------------------------------------------------- Postgres repository ---
def _row(**overrides: Any) -> PulseAlertRow:
    values: dict[str, Any] = {
        "id": uuid4(),
        "brand_id": BRAND_ID,
        "kind": "no_publish_7d",
        "severity": "warning",
        "payload": {"ref": "7d", "days": 7},
        "created_at": NOW,
        "acknowledged_at": None,
        "acknowledged_by_user_id": None,
    }
    values.update(overrides)
    return PulseAlertRow(**values)


async def test_pg_pulse_repository_alert_statements_and_mapping() -> None:
    row = _row()
    acked = _row(acknowledged_at=NOW, acknowledged_by_user_id=uuid4())
    session = StubSession(
        [
            StubResult(rows=[(BRAND_ID, "7d"), (BRAND2_ID, None)]),  # open_refs
            StubResult(scalars=[row, acked]),  # list_alerts (open)
            StubResult(scalars=[]),  # list_alerts (all)
            StubResult(scalar=row),  # acknowledge: update returning
            StubResult(scalar=None),  # acknowledge: update hit nothing
            StubResult(scalar=acked),  # … existing acked row
            StubResult(scalar=None),
            StubResult(scalar=None),  # … unknown
        ]
    )
    repo = PgPulseRepository(session)  # type: ignore[arg-type]
    assert await repo.open_refs("no_publish_7d") == {(BRAND_ID, "7d"), (BRAND2_ID, "")}
    sql = compiled(session.executed[0][0])
    assert "acknowledged_at IS NULL" in sql and "pulse_alerts.kind =" in sql

    alerts = await repo.list_alerts(BRAND_ID, open_only=True, limit=5)
    assert [a.id for a in alerts] == [row.id, acked.id] and alerts[0].payload == {
        "ref": "7d",
        "days": 7,
    }
    sql = compiled(session.executed[1][0])
    assert "pulse_alerts.brand_id =" in sql and "acknowledged_at IS NULL" in sql
    assert "ORDER BY pulse_alerts.created_at DESC" in sql and "LIMIT" in sql
    await repo.list_alerts(BRAND_ID, open_only=False, limit=5)
    assert "acknowledged_at IS NULL" not in compiled(session.executed[2][0])

    added = await repo.add_alerts(
        [
            Alert(
                brand_id=BRAND_ID,
                kind="no_publish_7d",
                severity="warning",
                payload={"ref": "7d"},
                created_at=NOW,
            )
        ]
    )
    assert len(added) == 1 and len(session.added) == 1 and session.flush_count == 1
    assert session.added[0].kind == "no_publish_7d" and session.added[0].payload == {"ref": "7d"}

    actor = uuid4()
    first = await repo.acknowledge(BRAND_ID, row.id, actor_user_id=actor, now=NOW)
    assert first is not None and first.id == row.id
    sql = compiled(session.executed[3][0])
    assert "UPDATE pulse_alerts" in sql and "acknowledged_at IS NULL" in sql and "RETURNING" in sql
    already = await repo.acknowledge(BRAND_ID, acked.id, actor_user_id=actor, now=NOW)
    assert already is not None and already.acknowledged_at == NOW
    assert await repo.acknowledge(BRAND_ID, uuid4(), actor_user_id=actor, now=NOW) is None


async def test_pg_pulse_repository_eight_checks_map_rows_to_findings() -> None:
    conv, job, conn, acct = uuid4(), uuid4(), uuid4(), uuid4()
    session = StubSession(
        [
            StubResult(rows=[(conv, BRAND_ID, NOW + timedelta(hours=1))]),
            StubResult(rows=[(BRAND_ID, 7300.5, 4)]),
            StubResult(rows=[(BRAND_ID,), (BRAND2_ID,)]),
            StubResult(rows=[(job, BRAND_ID, "permanent")]),
            StubResult(
                rows=[
                    (conn, BRAND_ID, NOW - timedelta(days=1), "active"),
                    (conn, BRAND2_ID, NOW + timedelta(days=3), "active"),
                    (uuid4(), BRAND2_ID, None, "reauth_required"),  # SPRINT-13 A-4
                ]
            ),
            StubResult(rows=[("id",), ("brand_id",)]),  # information_schema: no quality column
            StubResult(
                rows=[
                    (acct, BRAND_ID, 300, 240),
                    (acct, BRAND2_ID, 300, 100),
                    (acct, BRAND_ID, 10, 10),
                ]
            ),
        ]
    )
    repo = PgPulseRepository(session)  # type: ignore[arg-type]
    (w,) = await repo.window_closing(NOW, within_seconds=4 * 3600)
    assert w.kind == "wa_window_closing" and w.severity == "critical"
    assert w.payload == {
        "ref": str(conv),
        "conversation_id": str(conv),
        "window_expires_at": (NOW + timedelta(hours=1)).isoformat(),
    }
    assert session.executed[0][1] == {"now": NOW, "until": NOW + timedelta(hours=4)}

    (s,) = await repo.slow_replies(NOW, threshold_seconds=7200)
    assert s.kind == "wa_slow_replies" and s.payload == {
        "ref": "2026-03-01",
        "avg_reply_seconds": 7300,
        "samples": 4,
    }
    assert session.executed[1][1] == {"since": NOW - timedelta(days=7), "thr": 7200}

    n = await repo.no_publish(NOW, days=7)
    assert [f.brand_id for f in n] == [BRAND_ID, BRAND2_ID] and n[0].payload == {
        "ref": "7d",
        "days": 7,
    }

    (p,) = await repo.publish_failed(NOW, hours=24)
    assert p.payload == {"ref": str(job), "job_id": str(job), "error_class": "permanent"}
    assert session.executed[3][1] == {"since": NOW - timedelta(hours=24)}

    t = await repo.token_expiring(NOW, days=7)
    assert [f.severity for f in t] == ["critical", "warning", "critical"]
    assert t[0].payload["ref"] == str(conn) and t[0].payload["reason"] == "expiring"
    assert t[2].payload["reason"] == "refresh_failed" and t[2].payload["token_expires_at"] is None
    assert session.executed[4][1] == {"until": NOW + timedelta(days=7)}

    assert await repo.quality_degraded(NOW) == []  # column absent ⇒ documented deviation
    assert len(session.executed) == 6  # the _QUALITY statement was never issued

    d = await repo.daily_cap_near(NOW, ratio=0.8)
    assert [(f.brand_id, f.severity) for f in d] == [(BRAND_ID, "warning"), (BRAND_ID, "critical")]
    assert d[0].payload == {
        "ref": f"{acct}:2026-03-01",
        "account_id": str(acct),
        "cap": 300,
        "used": 240,
    }
    assert session.executed[6][1] == {
        "default_cap": 300,
        "day_start": NOW.replace(hour=0, minute=0),
    }
    # every check is SQL text over other modules' tables — no ORM of theirs
    texts = [str(stmt) for stmt, _ in session.executed]
    assert any("wa_conversations" in t for t in texts) and any("publish_jobs" in t for t in texts)
    assert any("social_connections" in t for t in texts) and any(
        "wa_number_caps" in t for t in texts
    )


async def test_pg_pulse_quality_check_runs_when_columns_exist() -> None:
    acct = uuid4()
    session = StubSession(
        [
            StubResult(rows=[("quality_rating",), ("messaging_limit",)]),
            StubResult(rows=[(acct, BRAND_ID, "RED"), (acct, BRAND2_ID, "YELLOW")]),
        ]
    )
    q = await PgPulseRepository(session).quality_degraded(NOW)  # type: ignore[arg-type]
    assert [f.severity for f in q] == ["critical", "warning"]
    assert q[0].payload == {"ref": str(acct), "account_id": str(acct), "rating": "RED"}


# ------------------------------------------------------- entitlements adapter (6) ---
class _Repo:
    def __init__(self, stored: dict[UUID, Any]) -> None:
        self.stored = stored

    async def get(self, brand_id: UUID) -> Any:
        return self.stored.get(brand_id)


class _Counters:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, str, datetime]] = []

    async def monthly_used(self, brand_id: UUID, counter: str, *, since: datetime) -> int:
        self.calls.append((brand_id, counter, since))
        return 42 if counter == "monthly_posts" else 7


async def test_plan_counter_usage_uses_trial_limits_without_a_row() -> None:
    from src.modules.entitlements.domain.models import BrandEntitlement, Plan
    from src.modules.entitlements.service import MONTH_WINDOW

    growth = BrandEntitlement.trial(BRAND2_ID).model_copy(update={"plan": Plan.GROWTH})
    counters_ = _Counters()
    rows = await plan_counter_usage(
        [BRAND_ID, BRAND2_ID],
        repo=_Repo({BRAND2_ID: growth}),
        counters=counters_,
        now=NOW,  # type: ignore[arg-type]
    )
    assert [(r.brand_id, r.counter, r.used) for r in rows] == [
        (BRAND_ID, "monthly_posts", 42),
        (BRAND_ID, "monthly_wa_conversations", 7),
        (BRAND2_ID, "monthly_posts", 42),
        (BRAND2_ID, "monthly_wa_conversations", 7),
    ]
    trial = BrandEntitlement.trial(BRAND_ID).limits
    assert rows[0].limit == trial.monthly_posts and rows[1].limit == trial.monthly_wa_conversations
    assert rows[2].limit == growth.limits.monthly_posts
    assert all(since == NOW - MONTH_WINDOW for _, _, since in counters_.calls)
    assert isinstance(rows[0], PlanCounterUsageRow)
    # structurally a ``pulse.domain.CounterUsage`` — the duck-typed port feeds check (6)
    usage = [
        CounterUsage(brand_id=r.brand_id, counter=r.counter, used=r.used, limit=r.limit)
        for r in rows
    ]
    assert {f.payload["counter"] for f in counter_findings(usage)} == {"monthly_posts"}


async def test_pg_plan_counter_usage_reader_scans_active_brands_only() -> None:
    session = StubSession([StubResult(rows=[(BRAND_ID,)])])
    reader = PgPlanCounterUsageReader(session)  # type: ignore[arg-type]
    rows = await reader.usage(NOW)  # no entitlement row / no usage rows in the stub ⇒ trial, 0
    assert [(r.brand_id, r.counter, r.used) for r in rows] == [
        (BRAND_ID, "monthly_posts", 0),
        (BRAND_ID, "monthly_wa_conversations", 0),
    ]
    assert rows[0].limit > 0
    stmt, params = session.executed[0]
    assert "wa_conversations" in str(stmt) and "publish_jobs" in str(stmt) and "UNION" in str(stmt)
    assert params["since"] < NOW
    # no active brand ⇒ no further query, empty usage
    empty = PgPlanCounterUsageReader(StubSession([StubResult(rows=[])]))  # type: ignore[arg-type]
    assert await empty.usage(NOW) == []


# ------------------------------------------------- SPRINT-13 B-1 check (9) ------
async def test_pg_pulse_repository_monthly_service_check_9_maps_rows() -> None:
    """Check (9): one ``warning`` per account at ≥ 800 billable service messages in the
    calendar month; ``ref`` = ``<account_id>:<YYYY-MM>`` so a new month re-arms it."""
    acct = uuid4()
    session = StubSession([StubResult(rows=[(acct, BRAND_ID, 800)])])
    repo = PgPulseRepository(session)  # type: ignore[arg-type]
    (m,) = await repo.monthly_service_near(NOW, threshold=800, cap=1000)
    assert m.kind == "wa_monthly_service_80pct" and m.severity == "warning"
    assert m.payload == {
        "ref": f"{acct}:2026-03",
        "account_id": str(acct),
        "count": 800,
        "cap": 1000,
    }
    params = session.executed[0][1]
    assert params == {"month_start": NOW.replace(day=1, hour=0, minute=0), "threshold": 800}
    sql = str(session.executed[0][0])
    assert "billable IS TRUE OR m.billable IS NULL" in sql
    assert "HAVING count(m.id) >= :threshold" in sql
    assert "wa_messages" in sql and "pricing_category" in sql
