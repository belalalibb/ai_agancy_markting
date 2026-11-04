"""DB-less coverage of :class:`PgLifecycleRepository` and the router's ``get_unsubscriber``
(SPRINT-13 Part C) — statement shape, row→domain mapping and the secret-column filter over
the scripted :class:`StubSession`; the integration suite runs the same code against PG.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import Request
from src.core.config import Settings
from src.core.security import TokenEncryptionService
from src.modules.lifecycle.domain import EXPORT_ENTITIES, BrandExport
from src.modules.lifecycle.infrastructure.postgres_repository import (
    _DROP_FRAGMENTS,
    _EXPORT_SQL,
    PgLifecycleRepository,
)
from src.modules.lifecycle.router import get_unsubscriber

from tests.fakes import StubResult, StubSession, compiled

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
BRAND, USER, EXPORT = uuid4(), uuid4(), uuid4()


def test_export_sql_covers_every_entity_and_filters_secret_columns() -> None:
    assert set(_EXPORT_SQL) == set(EXPORT_ENTITIES)
    for sql in _EXPORT_SQL.values():
        assert ":b" in sql and ":n" in sql and "row_to_json" in sql
    for fragment in ("token", "secret", "hash", "api_key", "consent", "share"):
        assert fragment in _DROP_FRAGMENTS


async def test_read_entity_drops_secret_shaped_columns() -> None:
    row = {
        "id": "x",
        "body": "مرحبا",
        "encrypted_page_token": "gAAAA",
        "share_token_hash": "h",
        "learning_consent": "none",
    }
    session = StubSession([StubResult(rows=[(row,)])])
    rows = await PgLifecycleRepository(session).read_entity("messages", BRAND, limit=10)  # type: ignore[arg-type]
    assert rows == [{"id": "x", "body": "مرحبا"}]
    _, params = session.executed[0]
    assert params == {"b": BRAND, "n": 11}  # limit + 1 ⇒ the service can detect overflow


async def test_add_and_get_export_round_trip_shape() -> None:
    export = BrandExport(
        id=EXPORT,
        brand_id=BRAND,
        status="ready",
        requested_by=USER,
        created_at=NOW,
        ready_at=NOW,
        sha256="ab" * 32,
        byte_size=3,
    )
    row = (EXPORT, BRAND, "ready", USER, NOW, NOW, "ab" * 32, 3, b"zip")
    session = StubSession([StubResult(), StubResult(rows=[row]), StubResult(rows=[])])
    repo = PgLifecycleRepository(session)  # type: ignore[arg-type]
    assert await repo.add_export(export, b"zip") is export
    insert_sql, insert_params = session.executed[0]
    assert "INSERT INTO brand_exports" in compiled(insert_sql)
    assert insert_params["blob"] == b"zip" and insert_params["sha"] == "ab" * 32

    found = await repo.get_export(BRAND, EXPORT, not_before=NOW - timedelta(hours=24))
    assert found is not None
    got, blob = found
    assert got == export and blob == b"zip"
    sql, params = session.executed[1]
    assert "created_at >= :not_before" in compiled(sql).replace("%(not_before)s", ":not_before")
    assert params == {"id": EXPORT, "b": BRAND, "not_before": NOW - timedelta(hours=24)}
    assert await repo.get_export(BRAND, EXPORT, not_before=NOW) is None


async def test_brand_names_page_tokens_and_delete() -> None:
    session = StubSession(
        [
            StubResult(rows=[("nile-coffee", "Nile Coffee")]),
            StubResult(rows=[]),
            StubResult(rows=[("p1", "enc-1"), ("p2", "enc-2")]),
            StubResult(rowcount=1),
            StubResult(rowcount=0),
        ]
    )
    repo = PgLifecycleRepository(session)  # type: ignore[arg-type]
    assert await repo.brand_names(BRAND) == ("nile-coffee", "Nile Coffee")
    assert await repo.brand_names(BRAND) is None
    assert await repo.page_tokens(BRAND) == [("p1", "enc-1"), ("p2", "enc-2")]
    assert "encrypted_page_token IS NOT NULL" in compiled(session.executed[2][0])
    assert await repo.delete_brand(BRAND) is True
    assert "DELETE FROM brands" in compiled(session.executed[3][0])
    assert await repo.delete_brand(BRAND) is False


@pytest.mark.parametrize("dry_run", [True, False])
async def test_sweep_counts_or_deletes_per_table_with_plan_aware_cutoff(dry_run: bool) -> None:
    results = [StubResult(scalar=n) if dry_run else StubResult(rowcount=n) for n in (4, 0, 1, 2)]
    session = StubSession(results)
    counts = await PgLifecycleRepository(session).sweep(  # type: ignore[arg-type]
        cutoff_default=NOW - timedelta(days=360),
        cutoff_agency=NOW - timedelta(days=720),
        export_cutoff=NOW - timedelta(hours=24),
        dry_run=dry_run,
    )
    assert counts == {
        "wa_messages": 4,
        "wa_reply_drafts": 0,
        "wa_conversations": 1,
        "brand_exports": 2,
    }
    statements = [compiled(s) for s, _ in session.executed]
    verb = "SELECT count(*)" if dry_run else "DELETE FROM"
    assert all(s.startswith(verb) for s in statements)
    assert all("e.plan = 'agency'" in s for s in statements[:3])  # 24 months for agency
    assert "brand_exports" in statements[3] and "agency" not in statements[3]
    forbidden = ("ai_cost_log", "entitlement_events", "approval_events", "admin_actions")
    assert not any(t in s for s in statements for t in forbidden)
    assert all(p["cd"] == NOW - timedelta(days=360) for _, p in session.executed)


# ------------------------------------------------------------------ get_unsubscriber ---
def _request(client: object | None) -> Request:
    app = SimpleNamespace(state=SimpleNamespace(http_client=client))
    return SimpleNamespace(app=app)  # type: ignore[return-value]


def test_get_unsubscriber_is_none_without_an_outbound_client(settings: Settings) -> None:
    assert get_unsubscriber(_request(None), settings) is None  # type: ignore[arg-type]
    assert (
        get_unsubscriber(SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())), settings)
        is None
    )  # type: ignore[arg-type]


async def test_get_unsubscriber_calls_subscribed_apps_delete_with_decrypted_token(
    settings: Settings, encryption: TokenEncryptionService
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        status = 200 if "p-ok" in request.url.path else 400
        return httpx.Response(status, json={"success": status == 200})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        unsubscribe = get_unsubscriber(_request(client), settings)  # type: ignore[arg-type]
        assert unsubscribe is not None
        token = encryption.encrypt_token("PAGE-TOKEN-1")
        assert await unsubscribe("p-ok", token) is True
        assert await unsubscribe("p-bad", token) is False
    assert [r.method for r in seen] == ["DELETE", "DELETE"]
    assert seen[0].url.path.endswith("/p-ok/subscribed_apps")
    assert seen[0].url.params["access_token"] == "PAGE-TOKEN-1"
    assert settings.meta_graph_api_version in seen[0].url.path
