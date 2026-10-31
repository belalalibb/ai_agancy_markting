"""SPRINT-10 B-1 — Brand Brain (M7): profile, revisions, competitors, evidence, AI cost log.

Tables (all constraints named, PostgreSQL round-trip tested by ``test_b1_m1``):

* ``brand_profiles``            — one row per brand, JSONB blocks, ``version`` bumped on
                                  every update (compare-and-set behind ``If-Match``);
* ``brand_profile_revisions``   — immutable snapshot per version (``source`` human /
                                  ai_suggested), UNIQUE(brand_id, version);
* ``brand_competitors``         — ≤3 per brand (service + brand row lock), legal public
                                  sources only (``allowed_hosts`` ≤3, host-only);
* ``brand_evidence``            — three-grade evidence (D-074) with the kind⇔source CHECK;
* ``ai_cost_log``               — metadata of every gateway call, **no text column**.

Revision ID: 0011
Revises: 0010
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid() -> postgresql.UUID:
    return postgresql.UUID(as_uuid=True)


def _ts() -> postgresql.TIMESTAMP:
    return postgresql.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "brand_profiles",
        sa.Column(
            "brand_id",
            _uuid(),
            sa.ForeignKey("brands.id", ondelete="CASCADE", name="fk_brand_profiles_brand_id"),
            primary_key=True,
        ),
        sa.Column(
            "products", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "audience", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("tone", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "goals", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "updated_by_user_id",
            _uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_brand_profiles_updated_by"),
            nullable=True,
        ),
        sa.Column("updated_at", _ts(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_brand_profiles_version"),
        sa.CheckConstraint(
            "jsonb_typeof(products) = 'array' AND jsonb_array_length(products) <= 20",
            name="ck_brand_profiles_products",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(goals) = 'array' AND jsonb_array_length(goals) <= 5",
            name="ck_brand_profiles_goals",
        ),
        sa.CheckConstraint("jsonb_typeof(audience) = 'object'", name="ck_brand_profiles_audience"),
        sa.CheckConstraint("jsonb_typeof(tone) = 'object'", name="ck_brand_profiles_tone"),
    )

    op.create_table(
        "brand_profile_revisions",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "brand_id",
            _uuid(),
            sa.ForeignKey(
                "brands.id", ondelete="CASCADE", name="fk_brand_profile_revisions_brand_id"
            ),
            nullable=False,
        ),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("snapshot", postgresql.JSONB, nullable=False),
        sa.Column(
            "changed_by_user_id",
            _uuid(),
            sa.ForeignKey(
                "users.id", ondelete="SET NULL", name="fk_brand_profile_revisions_changed_by"
            ),
            nullable=True,
        ),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.UniqueConstraint(
            "brand_id", "version", name="uq_brand_profile_revisions_brand_version"
        ),
        sa.CheckConstraint("version >= 1", name="ck_brand_profile_revisions_version"),
        sa.CheckConstraint(
            "source IN ('human', 'ai_suggested')", name="ck_brand_profile_revisions_source"
        ),
    )

    op.create_table(
        "brand_competitors",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "brand_id",
            _uuid(),
            sa.ForeignKey("brands.id", ondelete="CASCADE", name="fk_brand_competitors_brand_id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("facebook_page_id", sa.String(32), nullable=True),
        sa.Column("website_host", sa.String(253), nullable=True),
        sa.Column(
            "allowed_hosts",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "added_by_user_id",
            _uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT", name="fk_brand_competitors_added_by"),
            nullable=False,
        ),
        sa.Column("created_at", _ts(), nullable=False),
        sa.Column("competitor_refreshed_at", _ts(), nullable=True),
        sa.UniqueConstraint("brand_id", "name", name="uq_brand_competitors_brand_name"),
        sa.CheckConstraint("kind IN ('direct', 'indirect')", name="ck_brand_competitors_kind"),
        sa.CheckConstraint(
            "jsonb_typeof(allowed_hosts) = 'array' AND jsonb_array_length(allowed_hosts) <= 3",
            name="ck_brand_competitors_allowed_hosts",
        ),
        sa.CheckConstraint(
            "facebook_page_id IS NULL OR facebook_page_id ~ '^[0-9]{1,32}$'",
            name="ck_brand_competitors_facebook_page_id",
        ),
        sa.CheckConstraint(
            r"website_host IS NULL OR website_host !~ '[/:@?#\s]'",
            name="ck_brand_competitors_website_host",
        ),
    )

    op.create_table(
        "brand_evidence",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "brand_id",
            _uuid(),
            sa.ForeignKey("brands.id", ondelete="CASCADE", name="fk_brand_evidence_brand_id"),
            nullable=False,
        ),
        sa.Column("subject_kind", sa.String(16), nullable=False),
        sa.Column("subject_id", _uuid(), nullable=False),
        sa.Column("evidence_kind", sa.String(16), nullable=False),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("value", postgresql.JSONB, nullable=False),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=False),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("source_ref", sa.Text, nullable=True),
        sa.Column("observed_at", _ts(), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.CheckConstraint(
            "subject_kind IN ('brand', 'competitor')", name="ck_brand_evidence_subject_kind"
        ),
        sa.CheckConstraint(
            "evidence_kind IN ('measured', 'inferred', 'estimated')",
            name="ck_brand_evidence_evidence_kind",
        ),
        sa.CheckConstraint(
            "source_kind IN ('graph_api', 'cloud_api', 'internal_db', 'public_web', "
            "'owner_input', 'model_inference')",
            name="ck_brand_evidence_source_kind",
        ),
        sa.CheckConstraint(
            "(evidence_kind = 'measured' AND source_kind IN ('graph_api', 'cloud_api', "
            "'internal_db')) OR "
            "(evidence_kind = 'estimated' AND source_kind IN ('public_web', 'owner_input', "
            "'graph_api')) OR "
            "(evidence_kind = 'inferred' AND source_kind = 'model_inference')",
            name="ck_brand_evidence_kind_source",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_brand_evidence_confidence"
        ),
        sa.CheckConstraint("jsonb_typeof(value) = 'object'", name="ck_brand_evidence_value"),
    )
    op.create_index(
        "ix_brand_evidence_lookup",
        "brand_evidence",
        ["brand_id", "subject_kind", "subject_id", "metric", sa.text("observed_at DESC")],
    )
    op.create_index(
        "ix_brand_evidence_brand_observed",
        "brand_evidence",
        ["brand_id", sa.text("observed_at DESC"), "id"],
    )

    op.create_table(
        "ai_cost_log",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "brand_id",
            _uuid(),
            sa.ForeignKey("brands.id", ondelete="CASCADE", name="fk_ai_cost_log_brand_id"),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("credential_mode", sa.String(16), nullable=False),
        sa.Column("prompt_tokens", sa.Integer, nullable=True),
        sa.Column("completion_tokens", sa.Integer, nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("outcome", sa.String(8), nullable=False),
        sa.Column("error_category", sa.String(32), nullable=True),
        sa.Column("created_at", _ts(), nullable=False),
        sa.CheckConstraint("outcome IN ('ok', 'error')", name="ck_ai_cost_log_outcome"),
        sa.CheckConstraint(
            "(outcome = 'error') = (error_category IS NOT NULL)",
            name="ck_ai_cost_log_error_iff_category",
        ),
        sa.CheckConstraint(
            "credential_mode IN ('platform', 'user_key')", name="ck_ai_cost_log_credential_mode"
        ),
        sa.CheckConstraint(
            "(prompt_tokens IS NULL OR prompt_tokens >= 0) AND "
            "(completion_tokens IS NULL OR completion_tokens >= 0) AND latency_ms >= 0",
            name="ck_ai_cost_log_non_negative",
        ),
    )
    op.create_index(
        "ix_ai_cost_log_brand_created", "ai_cost_log", ["brand_id", sa.text("created_at DESC")]
    )


def downgrade() -> None:
    op.drop_index("ix_ai_cost_log_brand_created", table_name="ai_cost_log")
    op.drop_table("ai_cost_log")
    op.drop_index("ix_brand_evidence_brand_observed", table_name="brand_evidence")
    op.drop_index("ix_brand_evidence_lookup", table_name="brand_evidence")
    op.drop_table("brand_evidence")
    op.drop_table("brand_competitors")
    op.drop_table("brand_profile_revisions")
    op.drop_table("brand_profiles")
