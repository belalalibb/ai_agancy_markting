"""SPRINT-12.1 Part A on PostgreSQL 17 UTF8 — A-1 (review S-1) + A-2 (Batch Approval, D-055).

Real tables, real constraints: ``brand_recommendations`` (share stamp), ``content_items`` /
``approval_events`` (one row per approved item), ``brand_entitlements`` (plan refusals).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from src.core.config import Settings
from src.modules.entitlements.domain.models import DEFAULT_LIMITS, Plan
from tests.conftest import AGENCY_ID, BRAND_ID, OWNER_ID, STRANGER_ID, USER_BY_TENANT
from tests.integration.conftest import TABLES, TEST_DATABASE_URL, integration_headers, seed_tenancy

pytestmark = pytest.mark.integration

API = "/api/v1"
BRAIN = f"{API}/brands/{BRAND_ID}/brain"
CONTENT = f"{API}/brands/{BRAND_ID}/content"


def _run(sql: str, params: dict | None = None, *, scalar: bool = False) -> object:
    async def _go() -> object:
        eng = create_async_engine(TEST_DATABASE_URL)
        try:
            async with eng.begin() as conn:
                res = await conn.execute(text(sql), params or {})
                return res.scalar() if scalar else None
        finally:
            await eng.dispose()

    return asyncio.run(_go())


def _rows(sql: str, params: dict | None = None) -> list[tuple]:
    async def _go() -> list[tuple]:
        eng = create_async_engine(TEST_DATABASE_URL)
        try:
            async with eng.begin() as conn:
                return [tuple(r) for r in (await conn.execute(text(sql), params or {})).all()]
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


def _recommendation(*, title: str, status: str = "proposed") -> UUID:
    ev_id, rec_id = uuid4(), uuid4()
    now = datetime.now(tz=UTC)
    _run(
        "INSERT INTO brand_evidence (id, brand_id, subject_kind, subject_id, evidence_kind, "
        "metric, value, confidence, source_kind, source_ref, observed_at, created_at) VALUES "
        "(:id, :b, 'brand', :b, 'measured', 'posting_frequency_7d', '{\"count\": 3}'::jsonb, "
        "0.9, 'internal_db', NULL, :now, :now)",
        {"id": ev_id, "b": BRAND_ID, "now": now},
    )
    decided = status != "proposed"
    _run(
        "INSERT INTO brand_recommendations (id, brand_id, kind, title, rationale, evidence_ids, "
        "evidence_kinds_summary, status, decided_by_user_id, decided_at, provenance, created_at) "
        "VALUES (:id, :b, 'gap_action', :t, 'because', :ev, :summary, :st, :u, :dt, '{}'::jsonb, "
        ":now)",
        {
            "id": rec_id,
            "b": BRAND_ID,
            "t": title,
            "ev": json.dumps([str(ev_id)]),
            "summary": json.dumps({"measured": 1, "inferred": 0, "estimated": 0}),
            "st": status,
            "u": USER_BY_TENANT[OWNER_ID] if decided else None,
            "dt": now if decided else None,
            "now": now,
        },
    )
    return rec_id


def _approved_events() -> int:
    return int(
        _run("SELECT count(*) FROM approval_events WHERE action = 'approved'", scalar=True)  # type: ignore[arg-type]
    )


def _grant(grantee: UUID, role: str) -> None:
    _run(
        "INSERT INTO brand_access_grants (id, brand_id, grantee_tenant_id, role, "
        "granted_by_tenant_id, granted_at) VALUES (:id, :b, :g, :r, :t, now())",
        {"id": uuid4(), "b": BRAND_ID, "g": grantee, "r": role, "t": OWNER_ID},
    )


# ================================================================== A-1 (S-1) ===
def test_a1_dismissed_recommendations_are_never_public_on_pg(
    integration_app: FastAPI, integration_settings: Settings
) -> None:
    """Replays review probes P2/P3: a dismissed sibling is absent from ``/d/{token}``, sharing
    a dismissed recommendation is 409 with **no** stamp written, and a shared anchor that is
    dismissed afterwards turns its link into a 404."""
    _seed_world()
    anchor = _recommendation(title="Reply within 2 hours to 3 open chats")
    kept = _recommendation(title="Keep posting twice a week")
    dismissed = _recommendation(title="Dismissed secret plan", status="dismissed")
    owner = integration_headers(integration_settings, OWNER_ID)
    with TestClient(integration_app) as client:
        res = client.post(f"{BRAIN}/diagnoses/{dismissed}/share", headers=owner)
        assert res.status_code == 409, res.text
        assert res.json()["detail"]["error"] == "recommendation_dismissed"
        assert (
            _run(
                "SELECT count(*) FROM brand_recommendations WHERE share_token_hash IS NOT NULL",
                scalar=True,
            )
            == 0
        )

        res = client.post(f"{BRAIN}/diagnoses/{anchor}/share", headers=owner)
        assert res.status_code == 201, res.text
        token = res.json()["url"].rsplit("/d/", 1)[1]
        card = client.get(f"{API}/d/{token}")
        assert card.status_code == 200, card.text
        titles = [r["title"] for r in card.json()["recommendations"]]
        assert titles == ["Reply within hours to open chats", "Keep posting twice a week"]
        assert "Dismissed" not in card.text and "secret" not in card.text
        assert str(kept) not in card.text  # no identifiers either

        # the owner dismisses the anchor through the real route ⇒ the link dies (A-1)
        res = client.post(
            f"{BRAIN}/recommendations/{anchor}/dismiss",
            headers=owner,
        )
        assert res.status_code == 200, res.text
        assert (
            _run(
                "SELECT status FROM brand_recommendations WHERE id = :id",
                {"id": anchor},
                scalar=True,
            )
            == "dismissed"
        )
        res = client.get(f"{API}/d/{token}")
        assert res.status_code == 404 and res.json()["detail"]["error"] == "diagnosis_not_found"
        # and it cannot be re-shared either
        assert client.post(f"{BRAIN}/diagnoses/{anchor}/share", headers=owner).status_code == 409


# ========================================================= A-2 (Batch, D-055) ===
def _item(client: TestClient, headers: dict[str, str], *, submit: bool = True) -> str:
    res = client.post(
        f"{CONTENT}/items",
        json={"channel": "facebook_page", "body": "batch on pg"},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    item_id = res.json()["id"]
    if submit:
        assert client.post(f"{CONTENT}/items/{item_id}/submit", headers=headers).status_code == 200
    return item_id


def test_a2_batch_approval_fifty_mixed_items_on_pg(
    integration_app: FastAPI, integration_settings: Settings
) -> None:
    """§3-2: 50 mixed items ⇒ 200 with one line each; ``approval_events`` = approved only;
    51 ⇒ 422; VIEWER ⇒ 403; the trial ceiling (10/month) refuses per item; a suspended plan
    marks every item ``entitlement_plan_inactive`` instead of failing the request."""
    _seed_world()
    _grant(STRANGER_ID, "viewer")
    _grant(AGENCY_ID, "manager")
    owner = integration_headers(integration_settings, OWNER_ID)
    with TestClient(integration_app) as client:
        pending = [_item(client, owner) for _ in range(12)]
        drafts = [_item(client, owner, submit=False) for _ in range(3)]
        missing = [str(uuid4()) for _ in range(35)]
        ids = pending[:4] + missing[:10] + drafts + pending[4:] + missing[10:]
        assert len(ids) == 50

        res = client.post(
            f"{CONTENT}/approvals/batch", json={"item_ids": ids + [str(uuid4())]}, headers=owner
        )
        assert res.status_code == 422
        viewer = integration_headers(integration_settings, STRANGER_ID)
        res = client.post(f"{CONTENT}/approvals/batch", json={"item_ids": ids}, headers=viewer)
        assert res.status_code == 403 and res.json()["detail"]["error"] == "insufficient_role"
        assert _approved_events() == 0

        res = client.post(f"{CONTENT}/approvals/batch", json={"item_ids": ids}, headers=owner)
        assert res.status_code == 200, res.text
        body = res.json()
        by_id = {r["item_id"]: r for r in body["results"]}
        assert len(body["results"]) == 50 and set(by_id) == set(ids)
        # trial = 10 posts/month: the first ten pending approve, the last two hit the ceiling
        approved_ids = [i for i in pending if by_id[i]["status"] == "approved"]
        assert approved_ids == pending[:10]
        assert {by_id[i]["error"] for i in pending[10:]} == {"entitlement_quota_exceeded"}
        assert {by_id[i]["error"] for i in drafts} == {"invalid_transition"}
        # same order as the single-item route (H2): plan check **before** the lifecycle ⇒ an
        # unknown id is ``content_not_found`` while the ceiling is open, and
        # ``entitlement_quota_exceeded`` once it is reached (exactly what ``…/approve`` says)
        assert {by_id[i]["error"] for i in missing[:10]} == {"content_not_found"}
        assert {by_id[i]["error"] for i in missing[10:]} == {"entitlement_quota_exceeded"}
        assert body["approved"] == 10 and body["skipped"] == 40
        # D-055: exactly one ``approved`` event per approved item, nothing for the rest
        events = _rows(
            "SELECT item_id::text, count(*) FROM approval_events WHERE action = 'approved' "
            "GROUP BY item_id"
        )
        assert sorted(events) == sorted((i, 1) for i in approved_ids)
        statuses = dict(
            _rows(
                "SELECT id::text, status FROM content_items WHERE id = ANY(:ids)",
                {"ids": [UUID(i) for i in pending]},
            )
        )
        assert [statuses[i] for i in pending] == ["approved"] * 10 + ["pending_approval"] * 2

        # suspended plan ⇒ every remaining item is skipped with the plan code, request 200
        _run(
            "INSERT INTO brand_entitlements (brand_id, plan, limits, active_until, is_active) "
            "VALUES (:b, 'starter', :limits, NULL, false)",
            {"b": BRAND_ID, "limits": json.dumps(DEFAULT_LIMITS[Plan.STARTER].model_dump())},
        )
        res = client.post(
            f"{CONTENT}/approvals/batch", json={"item_ids": pending[10:]}, headers=owner
        )
        assert res.status_code == 200, res.text
        assert [(r["status"], r["error"]) for r in res.json()["results"]] == [
            ("skipped", "entitlement_plan_inactive"),
            ("skipped", "entitlement_plan_inactive"),
        ]
        assert _approved_events() == 10
