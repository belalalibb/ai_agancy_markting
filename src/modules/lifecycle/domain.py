"""Data lifecycle domain (SPRINT-13 Part C, D-081): export / delete / retention."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

ExportStatus = Literal["pending", "ready", "failed"]
EXPORT_ROW_LIMIT = 5_000  # C-1: synchronous export ⇒ 413 ``export_too_large`` above this
EXPORT_ENTITIES: tuple[str, ...] = (
    "brand",
    "profile",
    "evidence",
    "recommendations",
    "content_items",
    "approval_events",
    "conversations",
    "messages",
    "alerts",
)
RETAINED_FOREVER: tuple[str, ...] = (  # C-3: never swept (7 years)
    "ai_cost_log",
    "entitlement_events",
    "approval_events",
    "admin_actions",
)
BRAND_DELETED = "brand_deleted"
RETENTION_SWEEP = "retention_sweep"


class BrandExport(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    brand_id: UUID
    status: ExportStatus = "pending"
    requested_by: UUID
    created_at: datetime
    ready_at: datetime | None = None
    sha256: str | None = None
    byte_size: int | None = None


class RetentionReport(BaseModel):
    """Counts per table from one sweep; ``dry_run`` ⇒ nothing was deleted."""

    model_config = ConfigDict(frozen=True)

    dry_run: bool
    cutoff_default: datetime
    cutoff_agency: datetime
    export_cutoff: datetime
    counts: dict[str, int]


__all__ = [
    "BRAND_DELETED",
    "EXPORT_ENTITIES",
    "EXPORT_ROW_LIMIT",
    "RETAINED_FOREVER",
    "RETENTION_SWEEP",
    "BrandExport",
    "ExportStatus",
    "RetentionReport",
]
