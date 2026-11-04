"""PostgreSQL ``lifecycle`` repository (SPRINT-13 Part C, D-081) — owns ``brand_exports``;
everything else is SQL text over other modules' tables (same discipline as ``pulse``/``ops``).

* Export (C-1): ``row_to_json`` per entity, capped at ``limit + 1`` rows so the service can
  answer 413 ``export_too_large``; secret / consent / shared-text keys are dropped here.
* Delete (C-2): ``DELETE FROM brands`` — the FKs cascade (``ai_cost_log.brand_id`` ⇒ NULL).
* Retention (C-3): one WHERE fragment per table drives both the dry-run count and the delete.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.lifecycle.domain import BrandExport

# (entity, SQL) — ``:b`` = brand id, ``:n`` = limit + 1. Keys are filtered by ``_clean``.
_EXPORT_SQL: dict[str, str] = {
    "brand": "SELECT row_to_json(t) FROM brands t WHERE t.id = :b LIMIT :n",
    "profile": "SELECT row_to_json(t) FROM brand_profiles t WHERE t.brand_id = :b LIMIT :n",
    "evidence": "SELECT row_to_json(t) FROM brand_evidence t WHERE t.brand_id = :b LIMIT :n",
    "recommendations": (
        "SELECT row_to_json(t) FROM brand_recommendations t WHERE t.brand_id = :b LIMIT :n"
    ),
    "content_items": "SELECT row_to_json(t) FROM content_items t WHERE t.brand_id = :b LIMIT :n",
    "approval_events": (
        "SELECT row_to_json(t) FROM approval_events t WHERE t.item_id IN "
        "(SELECT id FROM content_items WHERE brand_id = :b) LIMIT :n"
    ),
    "conversations": (
        "SELECT row_to_json(t) FROM wa_conversations t WHERE t.brand_id = :b LIMIT :n"
    ),
    "messages": "SELECT row_to_json(t) FROM wa_messages t WHERE t.brand_id = :b LIMIT :n",
    "alerts": "SELECT row_to_json(t) FROM pulse_alerts t WHERE t.brand_id = :b LIMIT :n",
}
# Never exported: secrets, hashes, consent and anything about shared (sanitised) texts.
_DROP_FRAGMENTS = ("token", "secret", "hash", "api_key", "consent", "share", "sanitiz", "sanitis")

_INSERT_EXPORT = text(
    "INSERT INTO brand_exports (id, brand_id, status, requested_by, created_at, ready_at, "
    "sha256, byte_size, blob) VALUES (:id, :b, :status, :u, :created, :ready, :sha, :size, :blob)"
)
_GET_EXPORT = text(
    "SELECT id, brand_id, status, requested_by, created_at, ready_at, sha256, byte_size, blob "
    "FROM brand_exports WHERE id = :id AND brand_id = :b AND created_at >= :not_before"
)
_BRAND_NAMES = text("SELECT slug, name FROM brands WHERE id = :b")
_PAGE_TOKENS = text(
    "SELECT p.page_id, p.encrypted_page_token FROM social_connection_pages p "
    "JOIN social_connections c ON c.id = p.connection_id "
    "WHERE c.brand_id = :b AND p.encrypted_page_token IS NOT NULL"
)
_DELETE_BRAND = text("DELETE FROM brands WHERE id = :b")

# C-3: (table, WHERE fragment). ``:cd`` default cutoff, ``:ca`` agency cutoff, ``:ce`` exports.
_PLAN_CUTOFF = (
    "< (SELECT CASE WHEN e.plan = 'agency' THEN CAST(:ca AS timestamptz) "
    "ELSE CAST(:cd AS timestamptz) END FROM brands b LEFT JOIN brand_entitlements e "
    "ON e.brand_id = b.id WHERE b.id = t.brand_id)"
)
_RETENTION: tuple[tuple[str, str], ...] = (
    ("wa_messages", f"t.created_at {_PLAN_CUTOFF}"),
    ("wa_reply_drafts", f"t.created_at {_PLAN_CUTOFF}"),
    ("wa_conversations", f"t.updated_at {_PLAN_CUTOFF}"),
    ("brand_exports", "t.created_at < CAST(:ce AS timestamptz)"),
)


def _clean(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if not any(f in k.lower() for f in _DROP_FRAGMENTS)}


def _export(row: Any) -> BrandExport:
    return BrandExport(
        id=row[0],
        brand_id=row[1],
        status=row[2],
        requested_by=row[3],
        created_at=row[4],
        ready_at=row[5],
        sha256=row[6],
        byte_size=row[7],
    )


class PgLifecycleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- C-1 export ------------------------------------------------------------------------
    async def read_entity(self, entity: str, brand_id: uuid.UUID, *, limit: int) -> list[dict]:
        rows = await self._session.execute(
            text(_EXPORT_SQL[entity]), {"b": brand_id, "n": limit + 1}
        )
        return [_clean(dict(r[0])) for r in rows.all()]

    async def add_export(self, export: BrandExport, blob: bytes | None) -> BrandExport:
        await self._session.execute(
            _INSERT_EXPORT,
            {
                "id": export.id,
                "b": export.brand_id,
                "status": export.status,
                "u": export.requested_by,
                "created": export.created_at,
                "ready": export.ready_at,
                "sha": export.sha256,
                "size": export.byte_size,
                "blob": blob,
            },
        )
        return export

    async def get_export(
        self, brand_id: uuid.UUID, export_id: uuid.UUID, *, not_before: datetime
    ) -> tuple[BrandExport, bytes | None] | None:
        row = (
            await self._session.execute(
                _GET_EXPORT, {"id": export_id, "b": brand_id, "not_before": not_before}
            )
        ).first()
        return None if row is None else (_export(row), bytes(row[8]) if row[8] else None)

    # --- C-2 delete ------------------------------------------------------------------------
    async def brand_names(self, brand_id: uuid.UUID) -> tuple[str, str] | None:
        row = (await self._session.execute(_BRAND_NAMES, {"b": brand_id})).first()
        return None if row is None else (str(row[0]), str(row[1]))

    async def page_tokens(self, brand_id: uuid.UUID) -> list[tuple[str, str]]:
        rows = await self._session.execute(_PAGE_TOKENS, {"b": brand_id})
        return [(str(r[0]), str(r[1])) for r in rows.all()]

    async def delete_brand(self, brand_id: uuid.UUID) -> bool:
        result = await self._session.execute(_DELETE_BRAND, {"b": brand_id})
        return (result.rowcount or 0) == 1

    # --- C-3 retention ---------------------------------------------------------------------
    async def sweep(
        self,
        *,
        cutoff_default: datetime,
        cutoff_agency: datetime,
        export_cutoff: datetime,
        dry_run: bool,
    ) -> dict[str, int]:
        params = {"cd": cutoff_default, "ca": cutoff_agency, "ce": export_cutoff}
        counts: dict[str, int] = {}
        for table, where in _RETENTION:
            if dry_run:
                sql = f"SELECT count(*) FROM {table} t WHERE {where}"  # noqa: S608 - constants
                counts[table] = int((await self._session.execute(text(sql), params)).scalar_one())
            else:
                sql = f"DELETE FROM {table} t WHERE {where}"  # noqa: S608 - constants above
                result = await self._session.execute(text(sql), params)
                counts[table] = int(result.rowcount or 0)
        return counts


__all__ = ["PgLifecycleRepository"]
