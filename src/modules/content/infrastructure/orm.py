"""SQLAlchemy ORM tables for ``content`` (PostgreSQL dialect, migration 0005)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base


class ContentItemRow(Base):
    __tablename__ = "content_items"
    __table_args__ = (
        Index("ix_content_items_brand_status", "brand_id", "status"),
        Index(
            "ix_content_items_brand_scheduled_for",
            "brand_id",
            "scheduled_for",
            postgresql_where=text("status = 'scheduled'"),
        ),
        CheckConstraint(
            "channel IN ('facebook_page', 'instagram')", name="ck_content_items_channel"
        ),
        CheckConstraint(
            "status IN ('draft', 'pending_approval', 'approved', 'rejected', "
            "'scheduled', 'archived')",
            name="ck_content_items_status",
        ),
        CheckConstraint(
            "(status = 'scheduled') = (scheduled_for IS NOT NULL)",
            name="ck_content_items_scheduled_for_iff_scheduled",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), nullable=False
    )
    created_by_tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    target_page_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default="draft")
    current_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("content_revisions.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )
    scheduled_for: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class ContentRevisionRow(Base):
    __tablename__ = "content_revisions"
    __table_args__ = (
        UniqueConstraint("item_id", "revision_no", name="uq_content_revisions_item_no"),
        CheckConstraint(
            "char_length(body) BETWEEN 1 AND 5000", name="ck_content_revisions_body_length"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_items.id", ondelete="CASCADE"), nullable=False
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    media_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by_tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class ApprovalEventRow(Base):
    __tablename__ = "approval_events"
    __table_args__ = (
        Index("ix_approval_events_item_created", "item_id", "created_at"),
        CheckConstraint(
            "action IN ('submitted', 'approved', 'rejected', 'withdrawn')",
            name="ck_approval_events_action",
        ),
        CheckConstraint(
            "note IS NULL OR char_length(note) <= 1000", name="ck_approval_events_note_length"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_items.id", ondelete="CASCADE"), nullable=False
    )
    revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("content_revisions.id", ondelete="CASCADE"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
