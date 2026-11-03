"""SPRINT-12 C-1 on PostgreSQL 17 — the public diagnosis link round trip on the real
``brand_recommendations`` row: share ⇒ only ``sha256`` is stored (UNIQUE) ⇒ the public card
is served without auth and leaks nothing ⇒ revoke ⇒ 404; VIEWER/MANAGER cannot share."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from src.core.config import Settings
from tests.conftest import AGENCY_ID, BRAND_ID, OWNER_ID, STRANGER_ID
from tests.integration.conftest import TABLES, TEST_DATABASE_URL, integration_headers, seed_tenancy

pytestmark = pytest.mark.integration

API = "/api/v1"
BRAIN = f"{API}/brands/{BRAND_ID}/brain"
FORBIDDEN = ("competitor", "phone", "email", "http", "wa_id", "evidence_url")


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


def _recommendation(*, title: str, kind: str = "gap_action", grade: str = "measured") -> UUID:
    """One ``brand_evidence`` + one ``brand_recommendations`` row with private provenance."""
    ev_id, rec_id = uuid4(), uuid4()
    now = datetime.now(tz=UTC)
    source = {"measured": "internal_db", "inferred": "model_inference", "estimated": "public_web"}
    metric = {
        "measured": "posting_frequency_7d",
        "inferred": "tone_match_score",
        "estimated": "public_page_summary",
    }
    _run(
        "INSERT INTO brand_evidence (id, brand_id, subject_kind, subject_id, evidence_kind, "
        "metric, value, confidence, source_kind, source_ref, observed_at, created_at) VALUES "
        "(:id, :b, 'brand', :b, :k, :m, '{\"count\": 3}'::jsonb, 0.9, :s, NULL, :now, :now)",
        {
            "id": ev_id,
            "b": BRAND_ID,
            "k": grade,
            "m": metric[grade],
            "s": source[grade],
            "now": now,
        },
    )
    _run(
        "INSERT INTO brand_recommendations (id, brand_id, kind, title, rationale, evidence_ids, "
        "evidence_kinds_summary, status, provenance, created_at) VALUES (:id, :b, :kind, :t, "
        ":r, :ev, :summary, 'proposed', :prov, :now)",
        {
            "id": rec_id,
            "b": BRAND_ID,
            "kind": kind,
            "t": title,
            "r": "Competitor Acme posts daily at https://acme.example; phone +20 100 000 0000",
            "ev": json.dumps([str(ev_id)]),
            "summary": json.dumps({"measured": 0, "inferred": 0, "estimated": 0, grade: 1}),
            "prov": json.dumps(
                {
                    "kind": "ai_generated",
                    "generated_at": now.isoformat(),
                    "prompt_version": "brand_diagnosis_v1",
                    "provider": "openai",
                    "model": "gpt-x",
                    "credential_mode": "platform",
                    "evidence_url": "https://private.example/e",
                }
            ),
            "now": now,
        },
    )
    return rec_id


def test_c1_share_round_trip_on_pg(
    integration_app: FastAPI, integration_settings: Settings
) -> None:
    _seed_world()
    anchor = _recommendation(title="Reply within 2 hours to 3 open chats")
    for i in range(4):
        _recommendation(
            title=f"Post about product {i} — www.competitor-{i}.example",
            kind="content_plan_item",
            grade="inferred" if i % 2 else "estimated",
        )
    for grantee, role in ((AGENCY_ID, "manager"), (STRANGER_ID, "viewer")):
        _run(
            "INSERT INTO brand_access_grants (id, brand_id, grantee_tenant_id, role, "
            "granted_by_tenant_id, granted_at) VALUES (:id, :b, :g, :r, :t, now())",
            {"id": uuid4(), "b": BRAND_ID, "g": grantee, "r": role, "t": OWNER_ID},
        )
    owner = integration_headers(integration_settings, OWNER_ID)
    with TestClient(integration_app) as client:
        # MANAGER (AGENCY) and VIEWER (STRANGER) cannot share — OWNER only (403, not 404)
        for tenant in (AGENCY_ID, STRANGER_ID):
            res = client.post(
                f"{BRAIN}/diagnoses/{anchor}/share",
                headers=integration_headers(integration_settings, tenant),
            )
            assert res.status_code == 403, res.text
            assert res.json()["detail"]["error"] == "insufficient_role"
        assert (
            _run(
                "SELECT count(*) FROM brand_recommendations WHERE share_token_hash IS NOT NULL",
                scalar=True,
            )
            == 0
        )

        res = client.post(f"{BRAIN}/diagnoses/{anchor}/share", headers=owner)
        assert res.status_code == 201, res.text
        url = res.json()["url"]
        token = url.rsplit("/d/", 1)[1]
        (stored_hash, shared_at) = _rows(
            "SELECT share_token_hash, shared_at FROM brand_recommendations WHERE id = :id",
            {"id": anchor},
        )[0]
        assert stored_hash == hashlib.sha256(token.encode()).hexdigest()
        assert shared_at is not None
        # the raw token never reaches the database (hash only)
        assert (
            _run(
                "SELECT count(*) FROM brand_recommendations WHERE share_token_hash = :t",
                {"t": token},
                scalar=True,
            )
            == 0
        )

        # public card — no Authorization header, cache header, forbidden strings absent
        public = client.get(f"{API}/d/{token}")
        assert public.status_code == 200, public.text
        assert public.headers["cache-control"] == "public, max-age=300"
        body = public.json()
        assert body["watermark"] == f"Generated by {integration_settings.app_name}"
        assert body["title"] == "Reply within hours to open chats"
        assert 3 <= len(body["recommendations"]) <= 5
        assert body["evidence_grade"] == "measured"
        raw = public.text.lower().replace(body["cta_url"].lower(), "")
        for needle in FORBIDDEN:
            assert needle not in raw, needle
        assert "acme" not in raw and "example" not in raw

        # the share is visible to nobody else: the OWNER's protected listing is unchanged
        listing = client.get(f"{BRAIN}/recommendations", headers=owner)
        assert listing.status_code == 200 and len(listing.json()["items"]) == 5

        # revoke ⇒ columns NULL ⇒ public link 404
        res = client.delete(f"{BRAIN}/diagnoses/{anchor}/share", headers=owner)
        assert res.status_code == 204
        assert _rows(
            "SELECT share_token_hash, shared_at FROM brand_recommendations WHERE id = :id",
            {"id": anchor},
        ) == [(None, None)]
        res = client.get(f"{API}/d/{token}")
        assert res.status_code == 404 and res.json()["detail"]["error"] == "diagnosis_not_found"

        # unknown recommendation ⇒ 404 recommendation_not_found; no row touched
        res = client.post(f"{BRAIN}/diagnoses/{uuid4()}/share", headers=owner)
        assert res.status_code == 404
        assert res.json()["detail"]["error"] == "recommendation_not_found"
