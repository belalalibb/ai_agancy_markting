"""Lifecycle service (SPRINT-13 Part C, D-081) — export, delete, retention.

* **C-1 export**: synchronous inside the request for ≤ ``EXPORT_ROW_LIMIT`` rows per entity
  (else 413 ``export_too_large``); the ZIP holds one ``<entity>.json`` per entity plus
  ``manifest.json`` (counts + sha256 of every member). No secret / token / consent / shared
  text leaves the repository (filtered by the repository layer, re-checked here).
* **C-2 delete**: ``confirm`` must equal the brand slug or name literally (422
  ``confirmation_mismatch``); Meta page subscriptions are unsubscribed best-effort, then
  ``DELETE FROM brands`` cascades (``ai_cost_log.brand_id ⇒ NULL``); one ``admin_actions``
  row ``brand_deleted`` with ``brand_hash = sha256(brand_id)`` — never the id itself.
* **C-3 retention**: counts (dry-run, default) or deletes rows older than the plan window
  and expired exports; one ``admin_actions`` row ``retention_sweep`` with ``actor = NULL``.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import zipfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from src.modules.lifecycle.domain import (
    BRAND_DELETED,
    EXPORT_ENTITIES,
    EXPORT_ROW_LIMIT,
    RETENTION_SWEEP,
    BrandExport,
    RetentionReport,
)
from src.modules.lifecycle.exceptions import (
    ConfirmationMismatchError,
    ExportNotFoundError,
    ExportTooLargeError,
)

logger = logging.getLogger(__name__)

Unsubscriber = Callable[[str, str], Awaitable[bool]]  # (page_id, page_token) ⇒ ok
Audit = Callable[..., Awaitable[Any]]  # OpsService.record signature
FORBIDDEN_FRAGMENTS = ("access_token", "refresh_token", "api_key", "gAAAA")


def _now() -> datetime:
    return datetime.now(tz=UTC)


def brand_hash(brand_id: UUID) -> str:
    return hashlib.sha256(str(brand_id).encode("utf-8")).hexdigest()


def build_zip(
    entities: dict[str, list[dict[str, Any]]], *, brand_id: UUID, now: datetime
) -> bytes:
    """Deterministic ZIP: one JSON per entity + ``manifest.json`` (counts, sha256 per file)."""
    manifest: dict[str, Any] = {
        "brand_hash": brand_hash(brand_id),
        "exported_at": now.isoformat(),
        "counts": {},
        "sha256": {},
    }
    buf = io.BytesIO()
    stamp = (now.year, now.month, now.day, now.hour, now.minute, now.second)
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in EXPORT_ENTITIES:
            payload = json.dumps(entities.get(name, []), ensure_ascii=False, default=str)
            if any(f in payload for f in FORBIDDEN_FRAGMENTS):  # belt and braces (§3-12)
                msg = f"export of {name!r} would leak a secret-shaped value"
                raise RuntimeError(msg)
            data = payload.encode("utf-8")
            manifest["counts"][name] = len(entities.get(name, []))
            manifest["sha256"][name] = hashlib.sha256(data).hexdigest()
            zf.writestr(zipfile.ZipInfo(f"{name}.json", stamp), data)
        zf.writestr(
            zipfile.ZipInfo("manifest.json", stamp),
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        )
    return buf.getvalue()


class LifecycleService:
    def __init__(
        self,
        repo: Any,
        *,
        audit: Audit,
        unsubscriber: Unsubscriber | None = None,
        export_ttl_hours: int = 24,
    ) -> None:
        self._repo = repo
        self._audit = audit
        self._unsubscribe = unsubscriber
        self._ttl = timedelta(hours=export_ttl_hours)

    # --- C-1 -------------------------------------------------------------------------------
    async def export_brand(self, brand_id: UUID, *, requested_by: UUID) -> BrandExport:
        now = _now()
        entities: dict[str, list[dict[str, Any]]] = {}
        for name in EXPORT_ENTITIES:
            rows = await self._repo.read_entity(name, brand_id, limit=EXPORT_ROW_LIMIT)
            if len(rows) > EXPORT_ROW_LIMIT:
                raise ExportTooLargeError(f"{name} exceeds {EXPORT_ROW_LIMIT} rows")
            entities[name] = rows
        blob = build_zip(entities, brand_id=brand_id, now=now)
        export = BrandExport(
            brand_id=brand_id,
            status="ready",
            requested_by=requested_by,
            created_at=now,
            ready_at=now,
            sha256=hashlib.sha256(blob).hexdigest(),
            byte_size=len(blob),
        )
        await self._repo.add_export(export, blob)
        logger.info("lifecycle: export brand_hash=%s bytes=%d", brand_hash(brand_id), len(blob))
        return export

    async def get_export(
        self, brand_id: UUID, export_id: UUID
    ) -> tuple[BrandExport, bytes | None]:
        found = await self._repo.get_export(brand_id, export_id, not_before=_now() - self._ttl)
        if found is None:
            raise ExportNotFoundError()
        return found

    # --- C-2 -------------------------------------------------------------------------------
    async def delete_brand(self, brand_id: UUID, *, confirm: str, actor_user_id: UUID) -> None:
        names = await self._repo.brand_names(brand_id)
        if names is None:
            raise ExportNotFoundError("brand not found")  # mapped to 404 by the router
        slug, name = names
        if confirm.strip() not in (slug, name):
            raise ConfirmationMismatchError("confirm must be the brand slug or name")
        unsubscribe = "skipped"
        if self._unsubscribe is not None:
            unsubscribe = "ok"
            for page_id, token in await self._repo.page_tokens(brand_id):
                try:
                    if not await self._unsubscribe(page_id, token):
                        unsubscribe = "failed"
                except Exception:  # noqa: BLE001 - best effort, never blocks the delete
                    unsubscribe = "failed"
        if not await self._repo.delete_brand(brand_id):
            raise ExportNotFoundError("brand not found")
        await self._audit(
            actor_user_id=actor_user_id,
            action=BRAND_DELETED,
            target_kind="brand",
            target_id=brand_hash(brand_id),
            after={"brand_hash": brand_hash(brand_id), "unsubscribe": unsubscribe},
        )
        logger.info(
            "lifecycle: brand deleted brand_hash=%s unsubscribe=%s",
            brand_hash(brand_id),
            unsubscribe,
        )

    # --- C-3 -------------------------------------------------------------------------------
    async def retention_sweep(
        self,
        *,
        now: datetime | None = None,
        months: int,
        months_agency: int,
        export_hours: int,
        dry_run: bool,
    ) -> RetentionReport:
        now = now or _now()
        cutoff_default = now - timedelta(days=30 * months)
        cutoff_agency = now - timedelta(days=30 * months_agency)
        export_cutoff = now - timedelta(hours=export_hours)
        counts = await self._repo.sweep(
            cutoff_default=cutoff_default,
            cutoff_agency=cutoff_agency,
            export_cutoff=export_cutoff,
            dry_run=dry_run,
        )
        await self._audit(
            actor_user_id=None,
            action=RETENTION_SWEEP,
            target_kind="platform",
            target_id="retention",
            after={"dry_run": dry_run, "counts": counts},
        )
        return RetentionReport(
            dry_run=dry_run,
            cutoff_default=cutoff_default,
            cutoff_agency=cutoff_agency,
            export_cutoff=export_cutoff,
            counts=counts,
        )


__all__ = ["FORBIDDEN_FRAGMENTS", "LifecycleService", "brand_hash", "build_zip"]
