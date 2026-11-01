"""SQLAlchemy ORM tables for ``brand_brain`` (PostgreSQL dialect, migration 0011).

Every constraint is **named** so ``alembic downgrade`` can drop it and tests can assert it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True)


def _brand_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), nullable=False
    )


def _ts(*, nullable: bool = False) -> Mapped[datetime]:
    return mapped_column(TIMESTAMP(timezone=True), nullable=nullable)


def _jsonb(default_sql: str) -> Mapped[Any]:
    return mapped_column(JSONB, nullable=False, server_default=text(f"'{default_sql}'::jsonb"))


class BrandProfileRow(Base):
    __tablename__ = "brand_profiles"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_brand_profiles_version"),
        CheckConstraint(
            "jsonb_typeof(products) = 'array' AND jsonb_array_length(products) <= 20",
            name="ck_brand_profiles_products",
        ),
        CheckConstraint(
            "jsonb_typeof(goals) = 'array' AND jsonb_array_length(goals) <= 5",
            name="ck_brand_profiles_goals",
        ),
        CheckConstraint("jsonb_typeof(audience) = 'object'", name="ck_brand_profiles_audience"),
        CheckConstraint("jsonb_typeof(tone) = 'object'", name="ck_brand_profiles_tone"),
    )

    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), primary_key=True
    )
    products: Mapped[list[dict[str, Any]]] = _jsonb("[]")
    audience: Mapped[dict[str, Any]] = _jsonb("{}")
    tone: Mapped[dict[str, Any]] = _jsonb("{}")
    goals: Mapped[list[dict[str, Any]]] = _jsonb("[]")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = _ts()


class BrandProfileRevisionRow(Base):
    __tablename__ = "brand_profile_revisions"
    __table_args__ = (
        UniqueConstraint("brand_id", "version", name="uq_brand_profile_revisions_brand_version"),
        CheckConstraint("version >= 1", name="ck_brand_profile_revisions_version"),
        CheckConstraint(
            "source IN ('human', 'ai_suggested')", name="ck_brand_profile_revisions_source"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    brand_id: Mapped[uuid.UUID] = _brand_fk()
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    changed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = _ts()


class BrandCompetitorRow(Base):
    __tablename__ = "brand_competitors"
    __table_args__ = (
        UniqueConstraint("brand_id", "name", name="uq_brand_competitors_brand_name"),
        CheckConstraint("kind IN ('direct', 'indirect')", name="ck_brand_competitors_kind"),
        CheckConstraint(
            "jsonb_typeof(allowed_hosts) = 'array' AND jsonb_array_length(allowed_hosts) <= 3",
            name="ck_brand_competitors_allowed_hosts",
        ),
        CheckConstraint(
            "facebook_page_id IS NULL OR facebook_page_id ~ '^[0-9]{1,32}$'",
            name="ck_brand_competitors_facebook_page_id",
        ),
        CheckConstraint(
            "website_host IS NULL OR website_host !~ '[/:@?#\\s]'",
            name="ck_brand_competitors_website_host",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    brand_id: Mapped[uuid.UUID] = _brand_fk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    facebook_page_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    website_host: Mapped[str | None] = mapped_column(String(253), nullable=True)
    allowed_hosts: Mapped[list[str]] = _jsonb("[]")
    notes: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    added_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = _ts()
    competitor_refreshed_at: Mapped[datetime | None] = _ts(nullable=True)


class BrandEvidenceRow(Base):
    __tablename__ = "brand_evidence"
    __table_args__ = (
        Index(
            "ix_brand_evidence_lookup",
            "brand_id",
            "subject_kind",
            "subject_id",
            "metric",
            text("observed_at DESC"),
        ),
        Index("ix_brand_evidence_brand_observed", "brand_id", text("observed_at DESC"), "id"),
        CheckConstraint(
            "subject_kind IN ('brand', 'competitor')", name="ck_brand_evidence_subject_kind"
        ),
        CheckConstraint(
            "evidence_kind IN ('measured', 'inferred', 'estimated')",
            name="ck_brand_evidence_evidence_kind",
        ),
        CheckConstraint(
            "source_kind IN ('graph_api', 'cloud_api', 'internal_db', 'public_web', "
            "'owner_input', 'model_inference')",
            name="ck_brand_evidence_source_kind",
        ),
        # D-074: the grade is bounded by the source that produced it.
        CheckConstraint(
            "(evidence_kind = 'measured' AND source_kind IN ('graph_api', 'cloud_api', "
            "'internal_db')) OR "
            "(evidence_kind = 'estimated' AND source_kind IN ('public_web', 'owner_input', "
            "'graph_api')) OR "
            "(evidence_kind = 'inferred' AND source_kind = 'model_inference')",
            name="ck_brand_evidence_kind_source",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_brand_evidence_confidence"
        ),
        CheckConstraint("jsonb_typeof(value) = 'object'", name="ck_brand_evidence_value"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    brand_id: Mapped[uuid.UUID] = _brand_fk()
    subject_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    evidence_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    observed_at: Mapped[datetime] = _ts()
    created_at: Mapped[datetime] = _ts()


class AICostLogRow(Base):
    """Metadata of every gateway call — **no prompt / output text column** (guarded by
    ``test_cost_log_has_no_text_column``)."""

    __tablename__ = "ai_cost_log"
    __table_args__ = (
        Index("ix_ai_cost_log_brand_created", "brand_id", text("created_at DESC")),
        CheckConstraint("outcome IN ('ok', 'error')", name="ck_ai_cost_log_outcome"),
        CheckConstraint(
            "(outcome = 'error') = (error_category IS NOT NULL)",
            name="ck_ai_cost_log_error_iff_category",
        ),
        CheckConstraint(
            "credential_mode IN ('platform', 'user_key')", name="ck_ai_cost_log_credential_mode"
        ),
        CheckConstraint(
            "(prompt_tokens IS NULL OR prompt_tokens >= 0) AND "
            "(completion_tokens IS NULL OR completion_tokens >= 0) AND latency_ms >= 0",
            name="ck_ai_cost_log_non_negative",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    brand_id: Mapped[uuid.UUID] = _brand_fk()
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    credential_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(8), nullable=False)
    error_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = _ts()
