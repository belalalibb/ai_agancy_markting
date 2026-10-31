"""SQLAlchemy ORM tables for ``publishing`` (PostgreSQL dialect, migration 0006)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base


class PublishJobRow(Base):
    __tablename__ = "publish_jobs"
    __table_args__ = (
        Index(
            "uq_publish_jobs_active_item",
            "item_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'leased')"),
        ),
        Index("ix_publish_jobs_status_run_at", "status", "run_at"),
        Index("ix_publish_jobs_status_next_attempt_at", "status", "next_attempt_at"),
        Index("ix_publish_jobs_item_created", "item_id", "created_at"),
        CheckConstraint(
            "status IN ('queued', 'leased', 'succeeded', 'failed', 'cancelled')",
            name="ck_publish_jobs_status",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1", name="ck_publish_jobs_attempts"
        ),
        CheckConstraint(
            "(status = 'leased') = (leased_until IS NOT NULL AND lease_owner IS NOT NULL)",
            name="ck_publish_jobs_lease_iff_leased",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_items.id", ondelete="CASCADE"), nullable=False
    )
    revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    target_page_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    leased_until: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    external_post_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    external_permalink: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    scheduled_by_tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    scheduled_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class PublishAttemptRow(Base):
    __tablename__ = "publish_attempts"
    __table_args__ = (
        UniqueConstraint("job_id", "attempt_no", name="uq_publish_attempts_job_no"),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('succeeded', 'retryable_failure', "
            "'permanent_failure', 'ambiguous')",
            name="ck_publish_attempts_outcome",
        ),
        CheckConstraint("attempt_no >= 1", name="ck_publish_attempts_no"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("publish_jobs.id", ondelete="CASCADE"), nullable=False
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    graph_error_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    graph_error_subcode: Mapped[int | None] = mapped_column(Integer, nullable=True)
    graph_trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_post_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
