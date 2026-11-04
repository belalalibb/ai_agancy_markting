"""Data lifecycle domain (SPRINT-13 Part C, D-081): export / delete / retention."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

ExportStatus = Literal["pending", "ready", "failed"]
EXPORT_ROW_LIMIT = 5_000  # C-1: synchronous export ⇒ 413 ``export_too_large`` above this
# SPRINT-13.1 F-4: explicit per-entity column allowlist — no ``row_to_json(t)`` over whole
# rows, so secrets / hashes / consent / share columns can never be selected by accident.
EXPORT_COLUMNS: dict[str, tuple[str, ...]] = {
    "brand": (
        "id",
        "owner_tenant_id",
        "slug",
        "name",
        "suspended_at",
        "referred_by_brand_id",
        "created_at",
        "updated_at",
    ),
    "profile": (
        "brand_id",
        "products",
        "audience",
        "tone",
        "goals",
        "sector",
        "version",
        "updated_by_user_id",
        "updated_at",
    ),
    "evidence": (
        "id",
        "brand_id",
        "subject_kind",
        "subject_id",
        "evidence_kind",
        "metric",
        "value",
        "confidence",
        "source_kind",
        "source_ref",
        "observed_at",
        "created_at",
    ),
    "recommendations": (
        "id",
        "brand_id",
        "kind",
        "title",
        "rationale",
        "evidence_ids",
        "evidence_kinds_summary",
        "status",
        "decided_by_user_id",
        "decided_at",
        "materialized_item_id",
        "provenance",
        "created_at",
    ),
    "content_items": (
        "id",
        "brand_id",
        "created_by_tenant_id",
        "channel",
        "target_page_id",
        "status",
        "current_revision_id",
        "scheduled_for",
        "published_at",
        "created_at",
        "updated_at",
    ),
    "content_revisions": ("id", "item_id", "revision_no", "body", "media_refs", "created_at"),
    "approval_events": (
        "id",
        "item_id",
        "revision_id",
        "action",
        "actor_tenant_id",
        "actor_user_id",
        "note",
        "created_at",
    ),
    "conversations": (
        "id",
        "brand_id",
        "account_id",
        "wa_id",
        "profile_name",
        "last_inbound_at",
        "last_outbound_at",
        "window_expires_at",
        "unread_count",
        "status",
        "lead_stage",
        "lead_stage_set_by_user_id",
        "lead_stage_at",
        "created_at",
        "updated_at",
    ),
    "messages": (
        "id",
        "conversation_id",
        "brand_id",
        "direction",
        "wamid",
        "client_message_id",
        "kind",
        "body",
        "media_id",
        "template_name",
        "delivery_status",
        "error_code",
        "sent_by_user_id",
        "reply_draft_id",
        "platform_timestamp",
        "referral",
        "billable",
        "pricing_category",
        "created_at",
    ),
    "alerts": (
        "id",
        "brand_id",
        "kind",
        "severity",
        "payload",
        "created_at",
        "acknowledged_at",
        "acknowledged_by_user_id",
    ),
    "brand_competitors": (
        "id",
        "brand_id",
        "name",
        "kind",
        "facebook_page_id",
        "website_host",
        "allowed_hosts",
        "notes",
        "added_by_user_id",
        "created_at",
        "competitor_refreshed_at",
    ),
}
EXPORT_ENTITIES: tuple[str, ...] = tuple(EXPORT_COLUMNS)
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
    "EXPORT_COLUMNS",
    "EXPORT_ENTITIES",
    "EXPORT_ROW_LIMIT",
    "RETAINED_FOREVER",
    "RETENTION_SWEEP",
    "BrandExport",
    "ExportStatus",
    "RetentionReport",
]
