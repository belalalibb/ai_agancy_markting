"""SPRINT-12 B-1 — lead loop (D-079) + Pulse-SQL alerts + WhatsApp number caps (D-082).

* ``wa_conversations`` += ``lead_stage`` (CHECK, default ``new``), ``lead_stage_set_by_user_id``,
  ``lead_stage_at``.
* ``lead_attributions`` — measured / inferred / owner_input attribution of a conversation to a
  publish source (codes only, never customer text — D-069).
* ``pulse_alerts`` — the eight SQL-only operational checks; partial index on the open ones.
* ``wa_number_caps`` — per-number daily reply cap + kill switch (D-082).
* ``brand_recommendations`` += ``share_token_hash`` (UNIQUE) + ``shared_at`` — the directive
  names the table ``brand_diagnoses``; no such table exists (a diagnosis is persisted as its
  ``brand_recommendations`` rows), so the real table is the reference (§5-5, recorded in §6).

No FK from any M1–M7 table *into* the new tables. ``downgrade`` is the exact inverse — tested
``head ⇒ 0015 ⇒ head`` by ``test_b1_migration_0016_round_trip_on_pg``.

Revision ID: 0016
Revises: 0015
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEAD_STAGES: tuple[str, ...] = ("new", "qualified", "quoted", "won", "lost", "not_lead")
ATTRIBUTION_GRADES: tuple[str, ...] = ("measured", "inferred", "owner_input")
PULSE_KINDS: tuple[str, ...] = (
    "wa_window_closing",
    "wa_slow_replies",
    "no_publish_7d",
    "publish_failed_24h",
    "meta_token_expiring",
    "plan_counter_80pct",
    "wa_quality_degraded",
    "wa_daily_cap_80pct",
)
PULSE_SEVERITIES: tuple[str, ...] = ("info", "warning", "critical")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _uuid() -> postgresql.UUID:
    return postgresql.UUID(as_uuid=True)


def _ts() -> postgresql.TIMESTAMP:
    return postgresql.TIMESTAMP(timezone=True)


def _brand_fk(name: str) -> sa.Column:
    return sa.Column(
        "brand_id",
        _uuid(),
        sa.ForeignKey("brands.id", ondelete="CASCADE", name=name),
        nullable=False,
    )


def upgrade() -> None:
    # --- wa_conversations: lead stage ----------------------------------------------------
    op.add_column(
        "wa_conversations",
        sa.Column("lead_stage", sa.String(16), nullable=False, server_default="new"),
    )
    op.add_column(
        "wa_conversations", sa.Column("lead_stage_set_by_user_id", _uuid(), nullable=True)
    )
    op.add_column("wa_conversations", sa.Column("lead_stage_at", _ts(), nullable=True))
    op.create_check_constraint(
        "ck_wa_conversations_lead_stage", "wa_conversations", _in("lead_stage", LEAD_STAGES)
    )

    # --- lead_attributions -----------------------------------------------------------------
    op.create_table(
        "lead_attributions",
        sa.Column("id", _uuid(), primary_key=True),
        _brand_fk("fk_lead_attributions_brand_id"),
        sa.Column(
            "conversation_id",
            _uuid(),
            sa.ForeignKey(
                "wa_conversations.id",
                ondelete="CASCADE",
                name="fk_lead_attributions_conversation_id",
            ),
            nullable=False,
        ),
        sa.Column("grade", sa.String(16), nullable=False),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("source_ref", sa.String(128), nullable=False),
        sa.Column(
            "signals", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("created_at", _ts(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "conversation_id",
            "source_kind",
            "source_ref",
            name="uq_lead_attributions_conversation_source",
        ),
        sa.CheckConstraint(_in("grade", ATTRIBUTION_GRADES), name="ck_lead_attributions_grade"),
    )
    op.create_index(
        "ix_lead_attributions_brand_created", "lead_attributions", ["brand_id", "created_at"]
    )

    # --- pulse_alerts ----------------------------------------------------------------------
    op.create_table(
        "pulse_alerts",
        sa.Column("id", _uuid(), primary_key=True),
        _brand_fk("fk_pulse_alerts_brand_id"),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("created_at", _ts(), nullable=False, server_default=sa.func.now()),
        sa.Column("acknowledged_at", _ts(), nullable=True),
        sa.Column("acknowledged_by_user_id", _uuid(), nullable=True),
        sa.CheckConstraint(_in("kind", PULSE_KINDS), name="ck_pulse_alerts_kind"),
        sa.CheckConstraint(_in("severity", PULSE_SEVERITIES), name="ck_pulse_alerts_severity"),
    )
    op.create_index(
        "ix_pulse_alerts_brand_open",
        "pulse_alerts",
        ["brand_id", "kind"],
        postgresql_where=sa.text("acknowledged_at IS NULL"),
    )

    # --- wa_number_caps (D-082) --------------------------------------------------------------
    op.create_table(
        "wa_number_caps",
        sa.Column(
            "account_id",
            _uuid(),
            sa.ForeignKey(
                "whatsapp_accounts.id", ondelete="CASCADE", name="fk_wa_number_caps_account_id"
            ),
            primary_key=True,
        ),
        sa.Column("daily_reply_cap", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("kill_switch", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("updated_at", _ts(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("daily_reply_cap > 0", name="ck_wa_number_caps_daily_reply_cap"),
    )

    # --- brand_recommendations: public share (Part C uses it) -------------------------------
    op.add_column(
        "brand_recommendations", sa.Column("share_token_hash", sa.String(64), nullable=True)
    )
    op.add_column("brand_recommendations", sa.Column("shared_at", _ts(), nullable=True))
    op.create_unique_constraint(
        "uq_brand_recommendations_share_token_hash", "brand_recommendations", ["share_token_hash"]
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_brand_recommendations_share_token_hash", "brand_recommendations", type_="unique"
    )
    op.drop_column("brand_recommendations", "shared_at")
    op.drop_column("brand_recommendations", "share_token_hash")
    op.drop_table("wa_number_caps")
    op.drop_index("ix_pulse_alerts_brand_open", table_name="pulse_alerts")
    op.drop_table("pulse_alerts")
    op.drop_index("ix_lead_attributions_brand_created", table_name="lead_attributions")
    op.drop_table("lead_attributions")
    op.drop_constraint("ck_wa_conversations_lead_stage", "wa_conversations", type_="check")
    op.drop_column("wa_conversations", "lead_stage_at")
    op.drop_column("wa_conversations", "lead_stage_set_by_user_id")
    op.drop_column("wa_conversations", "lead_stage")
