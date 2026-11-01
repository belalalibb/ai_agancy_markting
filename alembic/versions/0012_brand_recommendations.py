"""SPRINT-10 B-3 — Brand Brain recommendations + the general learning signal.

Tables (all constraints named, PostgreSQL round-trip tested by ``test_b3_m1``):

* ``brand_recommendations`` — every AI proposal is a *proposed* row until a human with a
                              fresh session accepts / dismisses it (D-055). ``evidence_ids``
                              must cite at least one evidence row (CHECK), a decision needs an
                              actor (CHECK), a ``content_plan_item`` that was accepted points
                              at the draft it became (``materialized_item_id``);
* ``learning_signals``      — the feature-agnostic signal table (``purpose`` + ``subject_id``
                              + ``signal`` + JSONB features, **no text** — D-069).
                              ``wa_learning_signals`` stays untouched (migration deferred).

Revision ID: 0012
Revises: 0011
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RECOMMENDATION_KINDS = ("content_plan_item", "tone_adjustment", "reply_template", "gap_action")
RECOMMENDATION_STATUSES = ("proposed", "accepted", "dismissed", "materialized")
LEARNING_SIGNALS = (
    "accepted",
    "dismissed",
    "materialized",
    "approved_unchanged",
    "approved_after_edit",
    "rejected",
    "expired",
)


EMPTY_SUMMARY_SQL = '\'{"measured": 0, "inferred": 0, "estimated": 0}\'::jsonb'


def _uuid() -> postgresql.UUID:
    return postgresql.UUID(as_uuid=True)


def _ts() -> postgresql.TIMESTAMP:
    return postgresql.TIMESTAMP(timezone=True)


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "brand_recommendations",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "brand_id",
            _uuid(),
            sa.ForeignKey("brands.id", ondelete="CASCADE", name="fk_brand_recommendations_brand"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("rationale", sa.Text, nullable=False),
        sa.Column("evidence_ids", postgresql.JSONB, nullable=False),
        sa.Column(
            "evidence_kinds_summary",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text(EMPTY_SUMMARY_SQL),
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="proposed"),
        sa.Column(
            "decided_by_user_id",
            _uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_brand_recommendations_actor"),
            nullable=True,
        ),
        sa.Column("decided_at", _ts(), nullable=True),
        sa.Column(
            "materialized_item_id",
            _uuid(),
            sa.ForeignKey(
                "content_items.id", ondelete="SET NULL", name="fk_brand_recommendations_item"
            ),
            nullable=True,
        ),
        sa.Column(
            "provenance",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", _ts(), nullable=False),
        sa.CheckConstraint(
            _in("kind", RECOMMENDATION_KINDS), name="ck_brand_recommendations_kind"
        ),
        sa.CheckConstraint(
            _in("status", RECOMMENDATION_STATUSES), name="ck_brand_recommendations_status"
        ),
        sa.CheckConstraint(
            "char_length(rationale) <= 1200", name="ck_brand_recommendations_rationale_len"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_ids) = 'array' AND jsonb_array_length(evidence_ids) >= 1",
            name="ck_brand_recommendations_evidence_ids",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_kinds_summary) = 'object'",
            name="ck_brand_recommendations_kinds_summary",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(provenance) = 'object'", name="ck_brand_recommendations_provenance"
        ),
        # A decision (anything but proposed) needs an actor and a time; proposed has neither.
        sa.CheckConstraint(
            "(status = 'proposed' AND decided_by_user_id IS NULL AND decided_at IS NULL) OR "
            "(status <> 'proposed' AND decided_by_user_id IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_brand_recommendations_decision_actor",
        ),
        sa.CheckConstraint(
            "materialized_item_id IS NULL OR status = 'materialized'",
            name="ck_brand_recommendations_materialized_iff",
        ),
    )
    op.create_index(
        "ix_brand_recommendations_brand_status_created",
        "brand_recommendations",
        ["brand_id", "status", sa.text("created_at DESC")],
    )

    op.create_table(
        "learning_signals",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "brand_id",
            _uuid(),
            sa.ForeignKey("brands.id", ondelete="CASCADE", name="fk_learning_signals_brand"),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("subject_id", _uuid(), nullable=False),
        sa.Column("signal", sa.String(24), nullable=False),
        sa.Column(
            "features", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("credential_mode", sa.String(16), nullable=False),
        sa.Column("created_at", _ts(), nullable=False),
        sa.CheckConstraint(_in("signal", LEARNING_SIGNALS), name="ck_learning_signals_signal"),
        sa.CheckConstraint(
            "credential_mode IN ('platform', 'user_key')",
            name="ck_learning_signals_credential_mode",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(features) = 'object'", name="ck_learning_signals_features"
        ),
    )
    op.create_index(
        "ix_learning_signals_brand_purpose_created",
        "learning_signals",
        ["brand_id", "purpose", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_learning_signals_brand_purpose_created", table_name="learning_signals")
    op.drop_table("learning_signals")
    op.drop_index(
        "ix_brand_recommendations_brand_status_created", table_name="brand_recommendations"
    )
    op.drop_table("brand_recommendations")
