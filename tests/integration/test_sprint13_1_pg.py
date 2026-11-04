"""SPRINT-13.1-FIX on PostgreSQL — the planner's live probe (``sprints/SPRINT-13_REVIEW.md``)
replayed as tests: requeue keeps ``attempt_count`` (attempts 1..6, zero IntegrityError), the
three 409 edges (D-070 / active job / archived item), export + delete without
``TOKEN_ENCRYPTION_KEY`` and for a suspended brand's OWNER, revisions + competitors +
customer text containing "api_key"/"gAAAA" in the ZIP, the non-member platform admin's
entitlements page (three cards, valid ``account_id``), non-admin 404 on the new routes, and
``feature_pulse`` flipped off in ``platform_flags`` ⇒ ``pulse_step`` returns 0."""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from src.core.config import Settings, get_settings
from src.core.database import build_session_factory
from src.core.security import TokenEncryptionService
from src.modules.ops.guards import flag_active
from src.modules.ops.infrastructure.postgres_repository import PgOpsRepository
from src.modules.ops.service import PLATFORM_FLAGS
from src.modules.publishing.domain.models import ErrorClass, PublishOutcome
from src.modules.pulse.scheduler_step import pulse_step
from tests.conftest import (
    AGENCY_ID,
    BRAND_ID,
    OWNER_ID,
    OWNER_USER_ID,
    STRANGER_USER_ID,
    TEST_PASSWORD,
)
from tests.fakes import FakePublisher
from tests.integration.conftest import TEST_DATABASE_URL, integration_headers
from tests.integration.test_scheduler_pg import _scheduled_item, _scheduler, _seed_connection
from tests.integration.test_sprint12_b_pg import (
    _conversation,
    _outbound,
    _rows,
    _run,
    _seed_world,
    _wa_account,
)
from tests.integration.test_sprint13_a_pg import _actions, _failed_job

pytestmark = pytest.mark.integration

API = "/api/v1"
ADMIN = f"{API}/admin"
ADMIN_ENT = "/app/admin/entitlements"
SYSTEM_SECRETS = (b"access_token", b"refresh_token", b"encrypted_", b"_hash", b"consent")


# ------------------------------------------------------------------ fixtures ---
def _fresh_world() -> None:
    """SPRINT-12 ``_seed_world`` predates 0017: also empty the two ops tables so audit rows
    and flags from the previous test never leak into the next assertion."""
    _seed_world()
    _run("TRUNCATE admin_actions, platform_flags")


@pytest.fixture
def admin_app(integration_app: FastAPI) -> FastAPI:
    """Seeded world + OWNER as platform admin + cold flag cache."""
    _fresh_world()
    PLATFORM_FLAGS.invalidate()
    settings = integration_app.state.settings
    admin = settings.model_copy(update={"platform_admin_user_ids": [OWNER_USER_ID]})
    integration_app.dependency_overrides[get_settings] = lambda: admin
    integration_app.state.admin_settings = admin
    yield integration_app
    PLATFORM_FLAGS.invalidate()


def _async(fn):  # noqa: ANN001, ANN202 - run ``fn(session_factory)`` on a fresh loop + engine
    """Sync tests + one short-lived engine per call (asyncpg connections are loop-bound) —
    the same pattern as ``_pulse_run`` / ``_sweep`` in the sibling files."""

    async def go():  # noqa: ANN202
        eng = create_async_engine(TEST_DATABASE_URL)
        try:
            return await fn(build_session_factory(eng))
        finally:
            await eng.dispose()

    return asyncio.run(go())


def _job(job_id: UUID) -> tuple:
    return _rows(
        "SELECT status, attempt_count, max_attempts, last_error_class FROM publish_jobs "
        "WHERE id = :id",
        {"id": job_id},
    )[0]


def _attempt_nos(job_id: UUID) -> list[int]:
    return [
        r[0]
        for r in _rows(
            "SELECT attempt_no FROM publish_attempts WHERE job_id = :id ORDER BY attempt_no",
            {"id": job_id},
        )
    ]


# ------------------------------------------------------------------ F-1 cycle ---
def test_f1_requeue_keeps_attempt_continuity_1_to_6_without_integrity_error(
    admin_app: FastAPI, encryption: TokenEncryptionService
) -> None:
    """``run_once ×3`` ⇒ attempts 1..3 + ``failed`` ⇒ admin requeue ⇒ ``attempt_count=3,
    max_attempts=6`` ⇒ ``run_once ×3`` ⇒ attempts 4..6 ⇒ ``failed`` again, naturally."""
    settings: Settings = admin_app.state.admin_settings
    publisher = FakePublisher(default=PublishOutcome.failure(ErrorClass.TRANSIENT))
    start = datetime.now(tz=UTC)
    _async(lambda f: _seed_connection(f, encryption))
    item, job = _async(lambda f: _scheduled_item(f, run_at=start - timedelta(minutes=1)))

    async def cycles(factory, clock: datetime, n: int) -> int:  # noqa: ANN001
        scheduler = _scheduler(factory, publisher, settings, encryption)
        leased = 0
        for _ in range(n):
            leased += await scheduler.run_once(clock)
            clock += timedelta(hours=1)  # past every backoff step
        return leased

    assert _async(lambda f: cycles(f, start, 3)) == 3
    assert _job(job.id)[:3] == ("failed", 3, 3) and _attempt_nos(job.id) == [1, 2, 3]
    assert (
        _run("SELECT status FROM content_items WHERE id = :id", {"id": item.id}, scalar=True)
        == "publish_failed"
    )

    h = integration_headers(settings, OWNER_ID)
    with TestClient(admin_app) as client:
        res = client.post(f"{ADMIN}/publishing/jobs/{job.id}/requeue", headers=h)
    assert res.status_code == 200, res.text
    assert (res.json()["attempt_count"], res.json()["max_attempts"]) == (3, 6)
    assert _job(job.id)[:3] == ("queued", 3, 6)
    assert _attempt_nos(job.id) == [1, 2, 3]  # never deleted
    [(action, _, _, _, before, after)] = _actions(target_kind="publish_job")
    assert action == "job_requeued"
    assert (before["attempt_count"], after["attempt_count"], after["max_attempts"]) == (3, 3, 6)

    later = datetime.now(tz=UTC) + timedelta(hours=4)  # after next_attempt_at = requeue time
    assert _async(lambda f: cycles(f, later, 3)) == 3  # no IntegrityError on attempt_no
    assert _job(job.id)[:3] == ("failed", 6, 6)
    assert _attempt_nos(job.id) == [1, 2, 3, 4, 5, 6]
    assert _async(lambda f: cycles(f, later + timedelta(hours=3), 1)) == 0  # stays failed
    assert publisher.calls[job.id] == 6
    assert (
        _run("SELECT status FROM content_items WHERE id = :id", {"id": item.id}, scalar=True)
        == "publish_failed"
    )


# ------------------------------------------------------------------ 409 edges ---
def test_f1_ambiguous_outcome_is_409_manual_verification_required(admin_app: FastAPI) -> None:
    settings = admin_app.state.admin_settings
    job_id, item_id, _ = _failed_job()
    _run(
        "UPDATE publish_jobs SET last_error_class = 'ambiguous_outcome' WHERE id = :id",
        {"id": job_id},
    )
    with TestClient(admin_app) as client:
        res = client.post(
            f"{ADMIN}/publishing/jobs/{job_id}/requeue",
            headers=integration_headers(settings, OWNER_ID),
        )
    assert res.status_code == 409 and res.json()["detail"]["error"] == (
        "manual_verification_required"
    )
    assert _job(job_id)[0] == "failed"
    assert (
        _run("SELECT status FROM content_items WHERE id = :id", {"id": item_id}, scalar=True)
        == "publish_failed"
    )
    assert _actions(target_kind="publish_job") == []


def test_f2_owner_retry_then_admin_requeue_is_409_publish_in_progress(
    admin_app: FastAPI,
) -> None:
    settings = admin_app.state.admin_settings
    job_id, item_id, _ = _failed_job()
    # the owner already retried ⇒ a second, queued job owns the item (uq_publish_jobs_active)
    _run(
        "INSERT INTO publish_jobs (id, item_id, revision_id, brand_id, channel, target_page_id, "
        "run_at, status, attempt_count, max_attempts, scheduled_by_tenant_id, "
        "scheduled_by_user_id, created_at, updated_at) SELECT :new, item_id, revision_id, "
        "brand_id, channel, target_page_id, now(), 'queued', 0, 3, scheduled_by_tenant_id, "
        "scheduled_by_user_id, now(), now() FROM publish_jobs WHERE id = :old",
        {"new": uuid4(), "old": job_id},
    )
    _run(
        "UPDATE content_items SET status = 'scheduled', scheduled_for = now() WHERE id = :id",
        {"id": item_id},
    )
    with TestClient(admin_app) as client:
        res = client.post(
            f"{ADMIN}/publishing/jobs/{job_id}/requeue",
            headers=integration_headers(settings, OWNER_ID),
        )
    assert res.status_code == 409 and res.json()["detail"]["error"] == "publish_in_progress"
    assert _job(job_id)[0] == "failed" and _actions(target_kind="publish_job") == []


def test_f3_archived_item_is_409_item_not_publish_failed_and_job_stays_failed(
    admin_app: FastAPI,
) -> None:
    settings = admin_app.state.admin_settings
    job_id, item_id, _ = _failed_job()
    _run("UPDATE content_items SET status = 'archived' WHERE id = :id", {"id": item_id})
    with TestClient(admin_app) as client:
        res = client.post(
            f"{ADMIN}/publishing/jobs/{job_id}/requeue",
            headers=integration_headers(settings, OWNER_ID),
        )
    assert res.status_code == 409
    assert res.json()["detail"]["error"] == "item_not_publish_failed"
    assert _job(job_id)[:3] == ("failed", 3, 3)  # the job UPDATE rolled back with the request
    assert (
        _run("SELECT status FROM content_items WHERE id = :id", {"id": item_id}, scalar=True)
        == "archived"
    )
    assert _actions(target_kind="publish_job") == []
    assert (
        _run(
            "SELECT count(*) FROM approval_events WHERE item_id = :id",
            {"id": item_id},
            scalar=True,
        )
        == 0
    )


# ------------------------------------------------------------------ F-4 lifecycle ---
def test_f4_export_and_delete_without_encryption_key_and_with_http_client(
    integration_app: FastAPI, integration_encryption: TokenEncryptionService
) -> None:
    """S13-3: ``http_client`` present (lifespan) + ``token_encryption_key=None`` ⇒ 202 / 204,
    never ``.get_secret_value()`` on ``None``."""
    _fresh_world()
    _async(lambda f: _seed_connection(f, integration_encryption))  # page token present
    settings = integration_app.state.settings
    no_key = settings.model_copy(update={"token_encryption_key": None})
    integration_app.dependency_overrides[get_settings] = lambda: no_key
    h = integration_headers(settings, OWNER_ID)
    with TestClient(integration_app) as client:
        assert client.app.state.http_client is not None
        res = client.post(f"{API}/brands/{BRAND_ID}/export", headers=h)
        assert res.status_code == 202, res.text
        res = client.request(
            "DELETE", f"{API}/brands/{BRAND_ID}", json={"confirm": "nile-coffee"}, headers=h
        )
        assert res.status_code == 204, res.text
    assert _run("SELECT count(*) FROM brands WHERE id = :b", {"b": BRAND_ID}, scalar=True) == 0
    [(action, _, _, _, _, after)] = _actions(action="brand_deleted")
    assert action == "brand_deleted" and after["unsubscribe"] == "skipped"


def test_f4_export_has_revisions_competitors_and_customer_text_with_secret_words(
    integration_app: FastAPI, integration_encryption: TokenEncryptionService
) -> None:
    _fresh_world()
    item_id, rev_id = uuid4(), uuid4()
    _run(
        "INSERT INTO content_items (id, brand_id, created_by_tenant_id, channel, status) "
        "VALUES (:id, :b, :t, 'facebook_page', 'draft')",
        {"id": item_id, "b": BRAND_ID, "t": OWNER_ID},
    )
    body = "عرض الأسبوع: قهوة النيل ☕ — نص المسودة الكامل"
    _run(
        "INSERT INTO content_revisions (id, item_id, revision_no, body, provenance, "
        "created_by_tenant_id) VALUES (:id, :item, 1, :body, CAST(:prov AS JSONB), :t)",
        {"id": rev_id, "item": item_id, "body": body, "prov": '{"kind": "human"}', "t": OWNER_ID},
    )
    _run(
        "INSERT INTO brand_competitors (id, brand_id, name, kind, website_host, allowed_hosts, "
        "notes, added_by_user_id, created_at) VALUES (:id, :b, 'Cairo Beans', 'direct', "
        "'cairobeans.example', '[]'::jsonb, 'main rival', :u, now())",
        {"id": uuid4(), "b": BRAND_ID, "u": OWNER_USER_ID},
    )
    account_id = _wa_account(integration_encryption)
    conv_id = _conversation(account_id)
    _outbound(conv_id, 1)
    customer_text = "our api_key starts with gAAAA please check"
    _run(
        "INSERT INTO wa_messages (id, conversation_id, brand_id, direction, wamid, kind, body, "
        "delivery_status, created_at) VALUES (:id, :c, :b, 'inbound', :w, 'text', :body, "
        "'received', now())",
        {
            "id": uuid4(),
            "c": conv_id,
            "b": BRAND_ID,
            "w": f"wamid.S131.{uuid4()}",
            "body": customer_text,
        },
    )
    settings = integration_app.state.settings
    h = integration_headers(settings, OWNER_ID)
    with TestClient(integration_app) as client:
        res = client.post(f"{API}/brands/{BRAND_ID}/export", headers=h)
        assert res.status_code == 202, res.text  # S13-4: no 500 on customer text
        dl = client.get(f"{API}/brands/{BRAND_ID}/export/{res.json()['export_id']}", headers=h)
    assert dl.status_code == 200
    with zipfile.ZipFile(io.BytesIO(dl.content)) as zf:
        files = {name: zf.read(name) for name in zf.namelist()}
    revisions = json.loads(files["content_revisions.json"])
    assert [r["body"] for r in revisions] == [body] and revisions[0]["revision_no"] == 1
    assert set(revisions[0]) == {
        "id",
        "item_id",
        "revision_no",
        "body",
        "media_refs",
        "created_at",
    }
    competitors = json.loads(files["brand_competitors.json"])
    assert [c["name"] for c in competitors] == ["Cairo Beans"]
    assert customer_text in files["messages.json"].decode("utf-8")
    manifest = json.loads(files["manifest.json"])
    assert manifest["counts"]["content_revisions"] == 1
    assert manifest["counts"]["brand_competitors"] == 1
    blob = b"".join(v for k, v in files.items() if k != "manifest.json").lower()
    for fragment in SYSTEM_SECRETS:  # real system secrets / hashes: grep ⇒ 0
        assert fragment not in blob, fragment
    assert integration_encryption.encrypt_token("x")[:5].encode() == b"gAAAA"  # sanity
    # the only "gAAAA" in the archive is the customer's own words, never a Fernet token
    assert blob.count(b"gaaaa") == 1


def test_f4_suspended_brand_owner_can_still_export_and_delete(
    integration_app: FastAPI,
) -> None:
    _fresh_world()
    _run("UPDATE brands SET suspended_at = now() WHERE id = :b", {"b": BRAND_ID})
    settings = integration_app.state.settings
    h = integration_headers(settings, OWNER_ID)
    with TestClient(integration_app) as client:
        res = client.post(f"{API}/brands/{BRAND_ID}/export", headers=h)
        assert res.status_code == 202, res.text  # D-081: the mount is not under ``suspended``
        res = client.request(
            "DELETE", f"{API}/brands/{BRAND_ID}", json={"confirm": "Nile Coffee"}, headers=h
        )
        assert res.status_code == 204, res.text
    assert _run("SELECT count(*) FROM brands WHERE id = :b", {"b": BRAND_ID}, scalar=True) == 0


# ------------------------------------------------------------------ F-5 admin UI ---
def test_f5_non_member_platform_admin_sees_three_cards_and_saves_wa_caps(
    integration_app: FastAPI,
    integration_encryption: TokenEncryptionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S13-2: the STRANGER tenant's user is a platform admin with **no** grant on BRAND —
    the page must still render suspension / entitlement / wa-caps via the admin routes."""
    _fresh_world()
    account_id = _wa_account(integration_encryption)
    _run("UPDATE brands SET suspended_at = now() WHERE id = :b", {"b": BRAND_ID})
    # the UI router reads the lru-cached ``get_settings()`` ⇒ env + cache clear
    monkeypatch.setenv("PLATFORM_ADMIN_USER_IDS", str(STRANGER_USER_ID))
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    get_settings.cache_clear()
    admin = get_settings().model_copy(update={"database_url": TEST_DATABASE_URL})
    assert STRANGER_USER_ID in admin.platform_admin_user_ids
    integration_app.dependency_overrides[get_settings] = lambda: admin
    try:
        with TestClient(integration_app, follow_redirects=False) as client:
            res = client.post(
                "/app/login", data={"email": "stranger@example.com", "password": TEST_PASSWORD}
            )
            assert res.status_code == 303, res.text
            res = client.get(f"{ADMIN_ENT}?brand_id={BRAND_ID}")
            assert res.status_code == 200, res.text
            html = res.text
            assert 'data-card="suspension" data-suspended="true"' in html
            assert 'data-card="entitlement"' in html
            assert 'data-form="wa-caps"' in html
            assert f'name="account_id" value="{account_id}"' in html
            marker = 'name="csrf" value="'
            start = html.index(marker) + len(marker)
            csrf = html[start : html.index('"', start)]
            res = client.post(
                f"{ADMIN_ENT}/wa-caps",
                data={
                    "csrf": csrf,
                    "brand_id": str(BRAND_ID),
                    "account_id": str(account_id),
                    "daily_reply_cap": "50",
                    "kill_switch": "true",
                },
            )
            assert res.status_code == 200, res.text
    finally:
        get_settings.cache_clear()
    caps = _rows(
        "SELECT daily_reply_cap, kill_switch FROM wa_number_caps WHERE account_id = :a",
        {"a": account_id},
    )
    assert caps == [(50, True)]


def test_f5_non_admin_gets_404_on_both_admin_brand_routes(integration_app: FastAPI) -> None:
    _fresh_world()
    h = integration_headers(integration_app.state.settings, AGENCY_ID)
    with TestClient(integration_app) as client:
        for path in (f"{ADMIN}/brands/{BRAND_ID}", f"{ADMIN}/brands/{BRAND_ID}/whatsapp/summary"):
            res = client.get(path, headers=h)
            assert res.status_code == 404 and res.json()["detail"]["error"] == "not_found", path
    # and the admin sees ids + suspended only, no secrets
    PLATFORM_FLAGS.invalidate()
    settings = integration_app.state.settings
    admin = settings.model_copy(update={"platform_admin_user_ids": [OWNER_USER_ID]})
    integration_app.dependency_overrides[get_settings] = lambda: admin
    with TestClient(integration_app) as client:
        ah = integration_headers(admin, OWNER_ID)
        res = client.get(f"{ADMIN}/brands/{BRAND_ID}", headers=ah)
        assert res.status_code == 200 and res.json()["suspended"] is False
        assert res.json()["owner_tenant_id"] == str(OWNER_ID)
        res = client.get(f"{ADMIN}/brands/{BRAND_ID}/whatsapp/summary", headers=ah)
        assert res.status_code == 404  # no WhatsApp account yet ⇒ ``_optional`` ⇒ None


# ------------------------------------------------------------------ F-6 pulse flag ---
def test_f6_feature_pulse_off_in_platform_flags_skips_pulse_cycle(
    admin_app: FastAPI, integration_encryption: TokenEncryptionService
) -> None:
    settings: Settings = admin_app.state.admin_settings
    assert settings.feature_pulse is True
    account_id = _wa_account(integration_encryption)
    _run(
        "UPDATE whatsapp_accounts SET quality_rating = 'RED' WHERE id = :id", {"id": account_id}
    )  # check (7) would raise ``wa_quality_degraded`` if the cycle ran
    h = integration_headers(settings, OWNER_ID)
    with TestClient(admin_app) as client:
        res = client.put(f"{ADMIN}/flags/feature_pulse", json={"value": False}, headers=h)
        assert res.status_code == 200, res.text
    PLATFORM_FLAGS.invalidate()

    async def pulse_flag(session) -> bool:  # noqa: ANN001 - same shape as ``src/main.py``
        return await flag_active(PgOpsRepository(session), settings, "feature_pulse")

    def step_once(now: datetime) -> int:
        async def go(factory) -> int:  # noqa: ANN001
            step = pulse_step(
                interval_seconds=1, flag_reader=pulse_flag, session_factory=lambda: factory
            )
            return await step(now)

        return _async(go)

    now = datetime.now(tz=UTC)
    assert step_once(now) == 0
    assert _run("SELECT count(*) FROM pulse_alerts", scalar=True) == 0

    with TestClient(admin_app) as client:
        res = client.put(f"{ADMIN}/flags/feature_pulse", json={"value": True}, headers=h)
        assert res.status_code == 200
    PLATFORM_FLAGS.invalidate()
    assert step_once(now + timedelta(seconds=2)) >= 1  # the same reader now says "on"
    assert _run("SELECT count(*) FROM pulse_alerts", scalar=True) >= 1
