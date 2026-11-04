"""Ops domain (SPRINT-13 Part A): audit rows, platform flags, dead-letter jobs.

``AdminAction.before``/``after`` carry identifiers and counts only — never customer text
(D-069). Flags are read from the DB first and fall back to ``Settings`` (A-2) so existing
behaviour is unchanged until an admin flips a key.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

FlagKey = Literal["platform_readonly", "feature_pulse", "feature_lead_loop"]
FLAG_KEYS: tuple[str, ...] = FlagKey.__args__  # type: ignore[attr-defined]
FLAG_CACHE_SECONDS = 30
ADMIN_ACTIONS_MAX = 200
REQUEUED_BY_ADMIN = "requeued_by_admin"  # approval_events.action (D-055, migration 0017)
REQUEUE_GRACE_ATTEMPTS = 3  # SPRINT-13.1 F-1: max_attempts = attempt_count + grace, never reset


class AdminAction(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    actor_user_id: UUID | None  # None ⇒ system job (retention sweep)
    action: str = Field(min_length=1, max_length=32)
    target_kind: str = Field(min_length=1, max_length=32)
    target_id: str = Field(min_length=1, max_length=64)
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    at: datetime


class PlatformFlag(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: FlagKey
    value: bool
    updated_at: datetime | None = None
    source: Literal["db", "settings"] = "db"


class DeadLetterJob(BaseModel):
    """One ``publish_jobs.status = 'failed'`` row (ids/counts only)."""

    model_config = ConfigDict(frozen=True)

    job_id: UUID
    item_id: UUID
    brand_id: UUID
    channel: str
    error_class: str | None
    attempt_count: int
    max_attempts: int
    failed_at: datetime


class BrandSuspension(BaseModel):
    model_config = ConfigDict(frozen=True)

    brand_id: UUID
    suspended_at: datetime | None


class AdminBrandView(BaseModel):
    """Platform-admin brand card (SPRINT-13.1 F-5): ids + suspension only."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    name: str
    slug: str
    suspended_at: datetime | None
    owner_tenant_id: UUID

    @property
    def suspended(self) -> bool:
        return self.suspended_at is not None


class AdminWaSummary(BaseModel):
    """WhatsApp account summary for admins (F-5): no secrets, no hashes."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    display_phone: str | None
    status: str
    quality_rating: str | None
    messaging_limit: str | None


__all__ = [
    "ADMIN_ACTIONS_MAX",
    "FLAG_CACHE_SECONDS",
    "FLAG_KEYS",
    "REQUEUED_BY_ADMIN",
    "REQUEUE_GRACE_ATTEMPTS",
    "AdminAction",
    "AdminBrandView",
    "AdminWaSummary",
    "BrandSuspension",
    "DeadLetterJob",
    "FlagKey",
    "PlatformFlag",
]
