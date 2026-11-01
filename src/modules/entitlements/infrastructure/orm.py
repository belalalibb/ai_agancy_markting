"""SQLAlchemy tables owned by ``entitlements`` (migration 0014 — SPRINT-11 C-2).

``brand_entitlements`` — one row per brand (PK = ``brand_id``, CASCADE with the brand).
``entitlement_events`` — audit trail; ``brand_id`` is ``SET NULL`` on brand deletion
(D-081: the financial/admin history outlives the brand, anonymised).

The ``brands.referred_by_brand_id`` / ``brands.learning_consent`` / ``wa_messages.referral``
columns added by the same migration are mapped by their owning modules (``tenancy``,
``whatsapp``), not here.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base


class BrandEntitlementRow(Base):
    __tablename__ = "brand_entitlements"
    __table_args__ = (
        CheckConstraint(
            "plan IN ('trial', 'starter', 'growth', 'agency')", name="ck_brand_entitlements_plan"
        ),
        CheckConstraint("jsonb_typeof(limits) = 'object'", name="ck_brand_entitlements_limits"),
    )

    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), primary_key=True
    )
    plan: Mapped[str] = mapped_column(String(16), nullable=False)
    limits: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    active_until: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class EntitlementEventRow(Base):
    __tablename__ = "entitlement_events"
    __table_args__ = (
        Index("ix_entitlement_events_brand_created", "brand_id", "created_at"),
        CheckConstraint(
            "kind IN ('activated', 'extended', 'downgraded', 'suspended', 'referral_credit', "
            "'limits_overridden')",
            name="ck_entitlement_events_kind",
        ),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_entitlement_events_payload"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    brand_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
