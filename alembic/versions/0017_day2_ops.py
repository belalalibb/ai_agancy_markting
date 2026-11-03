"""SPRINT-13 — Day-2 Ops (one migration for Parts A + B + C, directive §5-3).

Part A — operational hardening
* ``admin_actions`` — one audit row per admin act (D-055); ``before``/``after`` hold ids and
  counts only (D-069). ``actor_user_id`` is NULL for system jobs (retention sweep).
* ``brands.suspended_at`` — brand kill switch (writes ⇒ 403 ``brand_suspended``).
* ``platform_flags`` — DB-held kill switches (``platform_readonly``, ``feature_pulse``,
  ``feature_lead_loop``) read before ``Settings``.
* ``approval_events.action`` += ``requeued_by_admin`` (dead-letter requeue, A-3).

Part B — WhatsApp economics (D-082/2)
* ``wa_messages.billable`` / ``pricing_category`` — filled from the webhook ``pricing`` block
  when Meta sends it (nullable; the monthly check counts ``billable IS NOT FALSE``).
* ``whatsapp_accounts.quality_rating`` / ``messaging_limit`` — read-only for the OWNER; the
  existing Pulse check (7) starts working once these columns exist.
* ``pulse_alerts.kind`` += ``wa_monthly_service_80pct`` (check 9).
* ``brand_profiles.sector`` — ``restaurants`` | ``clinics`` | NULL (decision أ).

Part C — data lifecycle (D-081)
* ``brand_exports`` — synchronous ZIP export kept 24 h (``blob`` BYTEA, sha256, size).

``downgrade`` is the exact inverse — ``head ⇒ 0016 ⇒ head`` is exercised on PG by
``test_migration_0017_round_trip_on_pg``.

Revision ID: 0017
Revises: 0016
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_PULSE_KINDS: tuple[str, ...] = (
    "wa_window_closing",
    "wa_slow_replies",
    "no_publish_7d",
    "publish_failed_24h",
    "meta_token_expiring",
    "plan_counter_80pct",
    "wa_quality_degraded",
    "wa_daily_cap_80pct",
)
NEW_PULSE_KINDS: tuple[str, ...] = (*OLD_PULSE_KINDS, "wa_monthly_service_80pct")
OLD_APPROVAL_ACTIONS: tuple[str, ...] = (
    "submitted",
    "approved",
    "rejected",
    "withdrawn",
    "scheduled",
    "published",
    "publish_failed",
    "publish_cancelled",
)
NEW_APPROVAL_ACTIONS: tuple[str, ...] = (*OLD_APPROVAL_ACTIONS, "requeued_by_admin")
SECTORS: tuple[str, ...] = ("restaurants", "clinics")
EXPORT_STATUSES: tuple[str, ...] = ("pending", "ready", "failed")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _uuid() -> postgresql.UUID:
    return postgresql.UUID(as_uuid=True)


def _ts() -> postgresql.TIMESTAMP:
    return postgresql.TIMESTAMP(timezone=True)


def _jsonb(name: str) -> sa.Column:
    return sa.Column(name, postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))


def upgrade() -> None:
    # --- A-1 admin_actions -------------------------------------------------------------------
    op.create_table(
        "admin_actions",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("actor_user_id", _uuid(), nullable=True),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("target_kind", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=False),
        _jsonb("before"),
        _jsonb("after"),
        sa.Column("at", _ts(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_admin_actions_target_at",
        "admin_actions",
        ["target_kind", "target_id", sa.text("at DESC")],
    )

    # --- A-2 kill switches -------------------------------------------------------------------
    op.add_column("brands", sa.Column("suspended_at", _ts(), nullable=True))
    op.create_table(
        "platform_flags",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Boolean(), nullable=False),
        sa.Column("updated_at", _ts(), nullable=False, server_default=sa.func.now()),
    )

    # --- A-3 requeue event (D-055) ----------------------------------------------------------
    op.drop_constraint("ck_approval_events_action", "approval_events", type_="check")
    op.create_check_constraint(
        "ck_approval_events_action", "approval_events", _in("action", NEW_APPROVAL_ACTIONS)
    )

    # --- B-1 billable WhatsApp messages + monthly check kind --------------------------------
    op.add_column("wa_messages", sa.Column("billable", sa.Boolean(), nullable=True))
    op.add_column("wa_messages", sa.Column("pricing_category", sa.String(32), nullable=True))
    op.drop_constraint("ck_pulse_alerts_kind", "pulse_alerts", type_="check")
    op.create_check_constraint(
        "ck_pulse_alerts_kind", "pulse_alerts", _in("kind", NEW_PULSE_KINDS)
    )

    # --- B-2 quality / messaging limit (read-only for the OWNER) ---------------------------
    op.add_column("whatsapp_accounts", sa.Column("quality_rating", sa.String(16), nullable=True))
    op.add_column("whatsapp_accounts", sa.Column("messaging_limit", sa.String(32), nullable=True))

    # --- B-3 brand sector --------------------------------------------------------------------
    op.add_column("brand_profiles", sa.Column("sector", sa.String(32), nullable=True))
    op.create_check_constraint(
        "ck_brand_profiles_sector", "brand_profiles", f"sector IS NULL OR {_in('sector', SECTORS)}"
    )

    # --- C-1 brand_exports (D-081) ----------------------------------------------------------
    op.create_table(
        "brand_exports",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "brand_id",
            _uuid(),
            sa.ForeignKey("brands.id", ondelete="CASCADE", name="fk_brand_exports_brand_id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("requested_by", _uuid(), nullable=False),
        sa.Column("created_at", _ts(), nullable=False, server_default=sa.func.now()),
        sa.Column("ready_at", _ts(), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=True),
        sa.Column("blob", postgresql.BYTEA, nullable=True),
        sa.CheckConstraint(_in("status", EXPORT_STATUSES), name="ck_brand_exports_status"),
    )
    op.create_index("ix_brand_exports_brand_created", "brand_exports", ["brand_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_brand_exports_brand_created", table_name="brand_exports")
    op.drop_table("brand_exports")
    op.drop_constraint("ck_brand_profiles_sector", "brand_profiles", type_="check")
    op.drop_column("brand_profiles", "sector")
    op.drop_column("whatsapp_accounts", "messaging_limit")
    op.drop_column("whatsapp_accounts", "quality_rating")
    op.execute(sa.text("DELETE FROM pulse_alerts WHERE kind = 'wa_monthly_service_80pct'"))
    op.drop_constraint("ck_pulse_alerts_kind", "pulse_alerts", type_="check")
    op.create_check_constraint(
        "ck_pulse_alerts_kind", "pulse_alerts", _in("kind", OLD_PULSE_KINDS)
    )
    op.drop_column("wa_messages", "pricing_category")
    op.drop_column("wa_messages", "billable")
    op.execute(sa.text("DELETE FROM approval_events WHERE action = 'requeued_by_admin'"))
    op.drop_constraint("ck_approval_events_action", "approval_events", type_="check")
    op.create_check_constraint(
        "ck_approval_events_action", "approval_events", _in("action", OLD_APPROVAL_ACTIONS)
    )
    op.drop_table("platform_flags")
    op.drop_column("brands", "suspended_at")
    op.drop_index("ix_admin_actions_target_at", table_name="admin_actions")
    op.drop_table("admin_actions")
