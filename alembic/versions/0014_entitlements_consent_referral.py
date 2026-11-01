"""SPRINT-11 C-2 — entitlements (D-073), learning consent (D-080), CTWA referral.

* ``brand_entitlements`` — plan + limits per brand (PK = brand_id, CASCADE).
* ``entitlement_events`` — audit trail; ``brand_id`` / ``actor_user_id`` ``SET NULL`` (D-081)
  + index ``(brand_id, created_at)``.
* ``brands.referred_by_brand_id`` UUID NULL FK brands SET NULL + ``CHECK (<> id)``.
* ``brands.learning_consent`` VARCHAR(16) NOT NULL DEFAULT ``'signals_only'``
  + ``CHECK IN ('signals_only', 'none', 'full')``.
* ``wa_messages.referral`` JSONB NULL (Meta Click-to-WhatsApp ``referral`` object, trimmed).

Round-trip tested on PostgreSQL by ``test_c1_migration_0014_round_trip_on_pg``.

Revision ID: 0014
Revises: 0013
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "brand_entitlements",
        sa.Column(
            "brand_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("brands.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("plan", sa.String(16), nullable=False),
        sa.Column("limits", postgresql.JSONB(), nullable=False),
        sa.Column("active_until", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "plan IN ('trial', 'starter', 'growth', 'agency')", name="ck_brand_entitlements_plan"
        ),
        sa.CheckConstraint("jsonb_typeof(limits) = 'object'", name="ck_brand_entitlements_limits"),
    )
    op.create_table(
        "entitlement_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "brand_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("brands.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "kind IN ('activated', 'extended', 'downgraded', 'suspended', 'referral_credit', "
            "'limits_overridden')",
            name="ck_entitlement_events_kind",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'", name="ck_entitlement_events_payload"
        ),
    )
    op.create_index(
        "ix_entitlement_events_brand_created", "entitlement_events", ["brand_id", "created_at"]
    )

    op.add_column(
        "brands",
        sa.Column("referred_by_brand_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_brands_referred_by_brand_id",
        "brands",
        "brands",
        ["referred_by_brand_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_brands_referred_by_not_self", "brands", "referred_by_brand_id <> id"
    )
    op.add_column(
        "brands",
        sa.Column(
            "learning_consent",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'signals_only'"),
        ),
    )
    op.create_check_constraint(
        "ck_brands_learning_consent",
        "brands",
        "learning_consent IN ('signals_only', 'none', 'full')",
    )

    op.add_column("wa_messages", sa.Column("referral", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("wa_messages", "referral")
    op.drop_constraint("ck_brands_learning_consent", "brands", type_="check")
    op.drop_column("brands", "learning_consent")
    op.drop_constraint("ck_brands_referred_by_not_self", "brands", type_="check")
    op.drop_constraint("fk_brands_referred_by_brand_id", "brands", type_="foreignkey")
    op.drop_column("brands", "referred_by_brand_id")
    op.drop_index("ix_entitlement_events_brand_created", table_name="entitlement_events")
    op.drop_table("entitlement_events")
    op.drop_table("brand_entitlements")
