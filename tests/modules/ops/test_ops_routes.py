"""SPRINT-13 Part A — ops admin routes, kill switches and the audit hook (DB-less).

* A-1: a successful admin act ⇒ exactly one ``admin_actions`` row; a failing audit write
  ⇒ the act is refused (same transaction — the row and the act share one session).
* A-2: ``platform_readonly`` ⇒ 503 + ``Retry-After`` on brand writes, ``/auth/*`` and
  ``/admin/*`` exempt, GET untouched; ``brands.suspended_at`` ⇒ 403 ``brand_suspended`` on
  writes, reads stay 200.
* A-3: dead-letter list + requeue CAS (``failed ⇒ queued``, attempt_count 0, D-055 event),
  409 ``job_not_failed`` for a queued job, 404 for an unknown one.
* A-4: ``GET /admin/alerts`` filters by kind; unknown kind ⇒ 422.
* Every ``/admin/*`` ops route answers **404** ``not_found`` for a non-admin (§3-6).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.core.config import Settings, get_settings
from src.modules.ops.domain import FLAG_KEYS, REQUEUE_GRACE_ATTEMPTS, DeadLetterJob
from src.modules.ops.service import PLATFORM_FLAGS, OpsService, PlatformFlags

from tests.conftest import AGENCY_ID, BRAND_ID, OWNER_ID, OWNER_USER_ID
from tests.fakes import FakeOpsRepository

API = "/api/v1"
ADMIN = f"{API}/admin"
Headers = Callable[[UUID], dict[str, str]]


@pytest.fixture
def admin_app(app: FastAPI, settings: Settings) -> FastAPI:
    admin_settings = settings.model_copy(update={"platform_admin_user_ids": [OWNER_USER_ID]})
    app.dependency_overrides[get_settings] = lambda: admin_settings
    return app


@pytest.fixture
def admin_client(admin_app: FastAPI) -> TestClient:
    PLATFORM_FLAGS.invalidate()
    with TestClient(admin_app) as c:
        yield c
    PLATFORM_FLAGS.invalidate()


@pytest.fixture
def ops_repo(admin_app: FastAPI) -> FakeOpsRepository:
    return admin_app.state.ops_repo


def _dead(job_id: UUID, *, status: str = "failed") -> dict:
    job = DeadLetterJob(
        job_id=job_id,
        item_id=uuid4(),
        brand_id=BRAND_ID,
        channel="facebook_page",
        error_class="permanent_failure",
        attempt_count=3,
        max_attempts=3,
        failed_at=datetime.now(tz=UTC),
    )
    return {"status": status, "revision_id": uuid4(), "job": job}


# ------------------------------------------------------------------ guard ---
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", f"{ADMIN}/actions"),
        ("POST", f"{ADMIN}/brands/{BRAND_ID}/suspend"),
        ("POST", f"{ADMIN}/brands/{BRAND_ID}/unsuspend"),
        ("GET", f"{ADMIN}/flags"),
        ("PUT", f"{ADMIN}/flags/platform_readonly"),
        ("GET", f"{ADMIN}/publishing/dead-letter"),
        ("POST", f"{ADMIN}/publishing/jobs/{uuid4()}/requeue"),
        ("GET", f"{ADMIN}/alerts"),
    ],
)
def test_non_admin_gets_404_on_every_ops_route(
    admin_client: TestClient, auth_headers: Headers, method: str, path: str
) -> None:
    res = admin_client.request(method, path, headers=auth_headers(AGENCY_ID), json={"value": True})
    assert res.status_code == 404 and res.json()["detail"]["error"] == "not_found"


# ------------------------------------------------------------------- A-2 -----
def test_flags_default_from_settings_then_db_wins_and_audits(
    admin_client: TestClient, auth_headers: Headers, ops_repo: FakeOpsRepository
) -> None:
    h = auth_headers(OWNER_ID)
    flags = {f["key"]: f for f in admin_client.get(f"{ADMIN}/flags", headers=h).json()}
    assert set(flags) == set(FLAG_KEYS)
    assert flags["platform_readonly"] == {
        "key": "platform_readonly",
        "value": False,
        "source": "settings",
        "updated_at": None,
    }
    assert (
        flags["feature_pulse"]["value"] is True and flags["feature_pulse"]["source"] == "settings"
    )

    res = admin_client.put(f"{ADMIN}/flags/platform_readonly", json={"value": True}, headers=h)
    assert res.status_code == 200 and res.json()["value"] is True and res.json()["source"] == "db"
    assert (
        admin_client.put(f"{ADMIN}/flags/nope", json={"value": True}, headers=h).status_code == 404
    )
    [row] = ops_repo.actions
    assert (row.action, row.target_kind, row.target_id) == (
        "flag_set",
        "platform_flag",
        "platform_readonly",
    )
    assert row.before == {"value": False, "source": "settings"} and row.after == {"value": True}
    assert row.actor_user_id == OWNER_USER_ID


def test_readonly_503_on_brand_writes_but_auth_admin_and_reads_pass(
    admin_client: TestClient, auth_headers: Headers, ops_repo: FakeOpsRepository
) -> None:
    h = auth_headers(OWNER_ID)
    admin_client.put(f"{ADMIN}/flags/platform_readonly", json={"value": True}, headers=h)
    body = {"channel": "facebook_page", "body": "hello", "target_page_id": "p1"}
    res = admin_client.post(f"{API}/brands/{BRAND_ID}/content/items", json=body, headers=h)
    assert res.status_code == 503, res.text
    assert res.headers["Retry-After"] == "300"
    assert res.json()["detail"]["error"] == "platform_readonly"
    # reads untouched; auth + admin exempt (the switch can be lifted)
    assert admin_client.get(f"{API}/brands/{BRAND_ID}", headers=h).status_code == 200
    login = admin_client.post(
        f"{API}/auth/login", json={"email": "nobody@example.com", "password": "x" * 12}
    )
    assert login.status_code in {401, 422, 429}
    res = admin_client.put(f"{ADMIN}/flags/platform_readonly", json={"value": False}, headers=h)
    assert res.status_code == 200 and res.json()["value"] is False
    res = admin_client.post(f"{API}/brands/{BRAND_ID}/content/items", json=body, headers=h)
    assert res.status_code == 201, res.text
    assert len(ops_repo.actions) == 2


def test_suspend_403_on_writes_reads_200_and_one_row_each(
    admin_client: TestClient, auth_headers: Headers, ops_repo: FakeOpsRepository
) -> None:
    h = auth_headers(OWNER_ID)
    res = admin_client.post(f"{ADMIN}/brands/{BRAND_ID}/suspend", headers=h)
    assert res.status_code == 200 and res.json()["suspended"] is True
    body = {"channel": "facebook_page", "body": "hello", "target_page_id": "p1"}
    res = admin_client.post(f"{API}/brands/{BRAND_ID}/content/items", json=body, headers=h)
    assert res.status_code == 403 and res.json()["detail"]["error"] == "brand_suspended"
    assert admin_client.get(f"{API}/brands/{BRAND_ID}/content/items", headers=h).status_code == 200
    res = admin_client.get(f"{API}/brands/{BRAND_ID}", headers=h)
    assert res.status_code == 200 and res.json()["suspended"] is True  # §3-9
    res = admin_client.post(f"{ADMIN}/brands/{BRAND_ID}/unsuspend", headers=h)
    assert res.status_code == 200 and res.json()["suspended_at"] is None
    assert admin_client.get(f"{API}/brands/{BRAND_ID}", headers=h).json()["suspended"] is False
    res = admin_client.post(f"{API}/brands/{BRAND_ID}/content/items", json=body, headers=h)
    assert res.status_code == 201
    assert admin_client.post(f"{ADMIN}/brands/{uuid4()}/suspend", headers=h).status_code == 404
    assert [a.action for a in ops_repo.actions] == ["brand_suspended", "brand_unsuspended"]
    assert ops_repo.actions[0].before == {"suspended": False}
    assert ops_repo.actions[0].after == {"suspended": True}


# ------------------------------------------------------------------- A-1 -----
def test_entitlement_write_audits_once_and_failed_audit_refuses_the_act(
    admin_client: TestClient, auth_headers: Headers, ops_repo: FakeOpsRepository
) -> None:
    """A-1 through another router's hook: ``POST /admin/entitlements/activate`` ⇒ one row;
    a 4xx act ⇒ no row; a failing audit write ⇒ the act itself is refused (one session)."""
    h = auth_headers(OWNER_ID)
    payload = {"brand_id": str(BRAND_ID), "plan": "growth", "months": 1}
    res = admin_client.post(f"{ADMIN}/entitlements/activate", json=payload, headers=h)
    assert res.status_code == 200, res.text
    [row] = ops_repo.actions
    assert row.action == "entitlement_activated" and row.target_id == str(BRAND_ID)
    assert row.after == {"plan": "growth", "months": 1}
    # a 4xx act writes nothing
    bad = {**payload, "brand_id": str(uuid4())}
    assert (
        admin_client.post(f"{ADMIN}/entitlements/activate", json=bad, headers=h).status_code == 404
    )
    assert len(ops_repo.actions) == 1
    # the audit write failing ⇒ the request fails (one transaction: no act without its row)
    ops_repo.fail_next_action = True
    with pytest.raises(RuntimeError, match="audit write failed"):
        admin_client.post(f"{ADMIN}/entitlements/activate", json=payload, headers=h)
    assert len(ops_repo.actions) == 1


def test_actions_list_filters_and_caps(
    admin_client: TestClient, auth_headers: Headers, ops_repo: FakeOpsRepository
) -> None:
    h = auth_headers(OWNER_ID)
    for key in ("feature_pulse", "feature_lead_loop"):
        admin_client.put(f"{ADMIN}/flags/{key}", json={"value": False}, headers=h)
    rows = admin_client.get(f"{ADMIN}/actions", headers=h).json()
    assert [r["target_id"] for r in rows] == ["feature_lead_loop", "feature_pulse"]  # newest first
    rows = admin_client.get(
        f"{ADMIN}/actions",
        params={"target_kind": "platform_flag", "target_id": "feature_pulse"},
        headers=h,
    ).json()
    assert len(rows) == 1 and rows[0]["actor_user_id"] == str(OWNER_USER_ID)
    assert (
        admin_client.get(f"{ADMIN}/actions", params={"limit": 201}, headers=h).status_code == 422
    )
    assert (
        admin_client.get(f"{ADMIN}/actions", params={"target_kind": "x"}, headers=h).json() == []
    )


# ------------------------------------------------------------------- A-3 -----
def test_dead_letter_requeue_keeps_attempt_count_and_extends_max(
    admin_client: TestClient, auth_headers: Headers, ops_repo: FakeOpsRepository
) -> None:
    """SPRINT-13.1 F-1: ``attempt_count`` is never reset (``attempt_no`` stays unique);
    ``max_attempts = attempt_count + REQUEUE_GRACE_ATTEMPTS`` in the response and audit."""
    h = auth_headers(OWNER_ID)
    failed, queued = uuid4(), uuid4()
    ops_repo.jobs[failed] = _dead(failed)
    ops_repo.jobs[queued] = _dead(queued, status="queued")
    rows = admin_client.get(f"{ADMIN}/publishing/dead-letter", headers=h).json()
    assert [r["job_id"] for r in rows] == [str(failed)]
    assert rows[0]["error_class"] == "permanent_failure" and rows[0]["attempt_count"] == 3

    res = admin_client.post(f"{ADMIN}/publishing/jobs/{failed}/requeue", headers=h)
    assert res.status_code == 200, res.text
    assert res.json()["attempt_count"] == 3
    assert res.json()["max_attempts"] == 3 + REQUEUE_GRACE_ATTEMPTS == 6
    assert ops_repo.jobs[failed]["status"] == "queued"
    assert ops_repo.requeue_events == [(ops_repo.jobs[failed]["job"].item_id, OWNER_USER_ID)]
    [row] = ops_repo.actions
    assert row.action == "job_requeued" and row.target_kind == "publish_job"
    assert row.before == {"status": "failed", "attempt_count": 3, "max_attempts": 3}
    assert row.after["status"] == "queued" and row.after["attempt_count"] == 3
    assert row.after["max_attempts"] == 6

    res = admin_client.post(f"{ADMIN}/publishing/jobs/{queued}/requeue", headers=h)
    assert res.status_code == 409 and res.json()["detail"]["error"] == "job_not_failed"
    res = admin_client.post(f"{ADMIN}/publishing/jobs/{failed}/requeue", headers=h)
    assert res.status_code == 409  # already requeued
    res = admin_client.post(f"{ADMIN}/publishing/jobs/{uuid4()}/requeue", headers=h)
    assert res.status_code == 404 and res.json()["detail"]["error"] == "publish_job_not_found"
    assert admin_client.get(f"{ADMIN}/publishing/dead-letter", headers=h).json() == []
    assert len(ops_repo.actions) == 1


def test_requeue_ambiguous_outcome_is_409_manual_verification_required(
    admin_client: TestClient, auth_headers: Headers, ops_repo: FakeOpsRepository
) -> None:
    """F-1 / D-070: an ``ambiguous_outcome`` job is never requeued blindly — nothing changes."""
    job_id = uuid4()
    row = _dead(job_id)
    row["job"] = row["job"].model_copy(update={"error_class": "ambiguous_outcome"})
    ops_repo.jobs[job_id] = row
    h = auth_headers(OWNER_ID)
    res = admin_client.post(f"{ADMIN}/publishing/jobs/{job_id}/requeue", headers=h)
    assert res.status_code == 409
    assert res.json()["detail"]["error"] == "manual_verification_required"
    assert ops_repo.jobs[job_id]["status"] == "failed"
    assert ops_repo.actions == [] and ops_repo.requeue_events == []


def test_requeue_with_active_job_on_item_is_409_publish_in_progress(
    admin_client: TestClient, auth_headers: Headers, ops_repo: FakeOpsRepository
) -> None:
    """F-2: owner already retried (a queued/leased job owns the item) ⇒ 409, no CAS."""
    job_id = uuid4()
    ops_repo.jobs[job_id] = _dead(job_id)
    ops_repo.active_items.add(ops_repo.jobs[job_id]["job"].item_id)
    h = auth_headers(OWNER_ID)
    res = admin_client.post(f"{ADMIN}/publishing/jobs/{job_id}/requeue", headers=h)
    assert res.status_code == 409 and res.json()["detail"]["error"] == "publish_in_progress"
    assert ops_repo.jobs[job_id]["status"] == "failed" and ops_repo.actions == []


def test_requeue_archived_item_is_409_item_not_publish_failed(
    admin_client: TestClient, auth_headers: Headers, ops_repo: FakeOpsRepository
) -> None:
    """F-3: the item CAS must hit exactly one row; otherwise 409 and zero audit rows."""
    job_id = uuid4()
    ops_repo.jobs[job_id] = _dead(job_id)
    ops_repo.item_status[ops_repo.jobs[job_id]["job"].item_id] = "archived"
    h = auth_headers(OWNER_ID)
    res = admin_client.post(f"{ADMIN}/publishing/jobs/{job_id}/requeue", headers=h)
    assert res.status_code == 409
    assert res.json()["detail"]["error"] == "item_not_publish_failed"
    assert ops_repo.actions == [] and ops_repo.requeue_events == []


# ------------------------------------------------------------------- F-5 -----
def test_admin_brand_view_and_wa_summary(
    admin_client: TestClient, auth_headers: Headers, ops_repo: FakeOpsRepository
) -> None:
    from src.modules.ops.domain import AdminWaSummary

    h = auth_headers(OWNER_ID)
    res = admin_client.get(f"{ADMIN}/brands/{BRAND_ID}", headers=h)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["id"] == str(BRAND_ID) and body["suspended"] is False
    assert set(body) == {"id", "name", "slug", "suspended", "suspended_at", "owner_tenant_id"}
    assert admin_client.get(f"{ADMIN}/brands/{uuid4()}", headers=h).status_code == 404

    ops_repo.suspended[BRAND_ID] = datetime.now(tz=UTC)
    assert admin_client.get(f"{ADMIN}/brands/{BRAND_ID}", headers=h).json()["suspended"] is True

    res = admin_client.get(f"{ADMIN}/brands/{BRAND_ID}/whatsapp/summary", headers=h)
    assert res.status_code == 404  # no account ⇒ the UI's ``_optional`` yields None
    account_id = uuid4()
    ops_repo.wa_summaries[BRAND_ID] = AdminWaSummary(
        id=account_id,
        display_phone="+20 10 0000 0000",
        status="active",
        quality_rating="GREEN",
        messaging_limit="TIER_1K",
    )
    res = admin_client.get(f"{ADMIN}/brands/{BRAND_ID}/whatsapp/summary", headers=h)
    assert res.status_code == 200
    assert res.json()["id"] == str(account_id) and res.json()["status"] == "active"
    assert set(res.json()) == {
        "id",
        "display_phone",
        "status",
        "quality_rating",
        "messaging_limit",
    }
    assert not any("token" in k or "secret" in k or "hash" in k for k in res.json())


# ------------------------------------------------------------------- A-4 -----
def test_admin_alerts_by_kind(
    admin_client: TestClient, auth_headers: Headers, ops_repo: FakeOpsRepository
) -> None:
    h = auth_headers(OWNER_ID)
    now = datetime.now(tz=UTC)
    ops_repo.alerts = [
        {
            "id": str(uuid4()),
            "brand_id": str(BRAND_ID),
            "kind": "meta_token_expiring",
            "severity": "critical",
            "payload": {"ref": "c1", "reason": "refresh_failed"},
            "created_at": now,
            "acknowledged_at": None,
        },
        {
            "id": str(uuid4()),
            "brand_id": str(BRAND_ID),
            "kind": "wa_quality_degraded",
            "severity": "warning",
            "payload": {"ref": "a1"},
            "created_at": now - timedelta(hours=1),
            "acknowledged_at": None,
        },
    ]
    rows = admin_client.get(f"{ADMIN}/alerts", headers=h).json()
    assert len(rows) == 1 and rows[0]["payload"]["reason"] == "refresh_failed"
    rows = admin_client.get(
        f"{ADMIN}/alerts", params={"kind": "wa_quality_degraded"}, headers=h
    ).json()
    assert [r["kind"] for r in rows] == ["wa_quality_degraded"]
    res = admin_client.get(f"{ADMIN}/alerts", params={"kind": "made_up"}, headers=h)
    assert res.status_code == 422 and res.json()["detail"]["error"] == "alert_kind_unknown"


# ------------------------------------------------------------- flag cache ---
async def test_platform_flags_cache_ttl_and_fallback(settings: Settings) -> None:
    repo = FakeOpsRepository()
    flags = PlatformFlags(ttl_seconds=30)
    clock = {"t": 100.0}
    tick = lambda: clock["t"]  # noqa: E731
    await flags.refresh(repo.get_flags, clock=tick)
    await flags.refresh(repo.get_flags, clock=tick)
    assert repo.flag_reads == 1  # cached
    clock["t"] += 31
    await flags.refresh(repo.get_flags, clock=tick)
    assert repo.flag_reads == 2
    assert flags.resolve("feature_pulse", settings).source == "settings"
    service = OpsService(repo, flags=flags)
    await service.set_flag("feature_pulse", False, actor_user_id=OWNER_USER_ID, settings=settings)
    stored = {f.key: f for f in await service.flags(settings)}
    assert stored["feature_pulse"].value is False and stored["feature_pulse"].source == "db"
    with pytest.raises(Exception, match="unknown flag"):
        flags.resolve("nope", settings)
    reads = repo.flag_reads  # the service used the real clock above — count from here
    flags.mark_loaded(clock=tick)  # a failed read keeps the last rows for one TTL
    await flags.refresh(repo.get_flags, clock=tick)
    assert repo.flag_reads == reads


# ------------------------------------------------- A-2 feature flags from DB -----
def test_db_feature_flags_switch_pulse_and_lead_loop_off(
    admin_client: TestClient, auth_headers: Headers, ops_repo: FakeOpsRepository
) -> None:
    """``feature_pulse=false`` in ``platform_flags`` ⇒ the Pulse routes answer 404
    ``pulse_disabled`` although ``Settings.feature_pulse`` is still ``True``; flipping the
    row back re-enables them. ``feature_lead_loop`` is resolved through the same port."""
    from src.modules.pulse.router import get_pulse_repository

    class _NoAlerts:
        async def list_alerts(self, *_a: object, **_k: object) -> list[object]:
            return []

    admin_client.app.dependency_overrides[get_pulse_repository] = _NoAlerts  # type: ignore[attr-defined]
    h = auth_headers(OWNER_ID)
    url = f"{API}/brands/{BRAND_ID}/pulse/alerts"
    assert admin_client.get(url, headers=h).status_code == 200
    res = admin_client.put(f"{ADMIN}/flags/feature_pulse", json={"value": False}, headers=h)
    assert res.status_code == 200 and res.json()["source"] == "db"
    res = admin_client.get(url, headers=h)
    assert res.status_code == 404 and res.json()["detail"]["error"] == "pulse_disabled"
    admin_client.put(f"{ADMIN}/flags/feature_pulse", json={"value": True}, headers=h)
    assert admin_client.get(url, headers=h).status_code == 200
    flags = {f["key"]: f for f in admin_client.get(f"{ADMIN}/flags", headers=h).json()}
    assert flags["feature_lead_loop"]["source"] == "settings"
    admin_client.put(f"{ADMIN}/flags/feature_lead_loop", json={"value": False}, headers=h)
    flags = {f["key"]: f for f in admin_client.get(f"{ADMIN}/flags", headers=h).json()}
    assert flags["feature_lead_loop"] == {
        "key": "feature_lead_loop",
        "value": False,
        "source": "db",
        "updated_at": flags["feature_lead_loop"]["updated_at"],
    }
    assert [a.target_id for a in ops_repo.actions] == [
        "feature_pulse",
        "feature_pulse",
        "feature_lead_loop",
    ]


# ------------------------------------------------- A-2 guard fallbacks -----------
async def test_flag_active_db_blip_falls_back_to_settings(settings: Settings) -> None:
    """A failing ``platform_flags`` read never becomes a global 503: the guard logs, keeps the
    previous rows for one TTL and answers from ``Settings``."""
    from src.modules.ops.guards import flag_active, readonly_active

    class _Broken:
        async def get_flags(self) -> dict[str, object]:
            raise RuntimeError("db down")

    flags = PlatformFlags(ttl_seconds=30)
    assert await readonly_active(_Broken(), settings, flags=flags) is False
    assert await flag_active(_Broken(), settings, "feature_pulse", flags=flags) is True


def test_failed_audit_write_refuses_the_flag_set(
    admin_client: TestClient, auth_headers: Headers, ops_repo: FakeOpsRepository
) -> None:
    ops_repo.fail_next_action = True
    with pytest.raises(RuntimeError, match="audit write failed"):
        admin_client.put(
            f"{ADMIN}/flags/platform_readonly",
            json={"value": True},
            headers=auth_headers(OWNER_ID),
        )
    assert ops_repo.actions == []
