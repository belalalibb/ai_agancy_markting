"""content — content_items / content_revisions / approval_events + scopes_verified

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-30

SPRINT-06 (Part A A-1 + Part B-1 B1).

upgrade()
---------
1. ``social_connections.scopes_verified BOOLEAN NOT NULL DEFAULT false`` (A-1): ``True`` iff
   ``granted_scopes`` came from a successful ``/debug_token``. Backfill: rows that already
   hold a non-empty scope list were necessarily filled by debug_token ⇒ ``true``.
2. ``content_items`` — one draft/post per brand+channel; ``status`` CHECK over the six
   lifecycle states; ``(status='scheduled') = (scheduled_for IS NOT NULL)``;
   ``current_revision_id`` FK added *after* ``content_revisions`` exists (circular).
3. ``content_revisions`` — immutable body versions (``UNIQUE(item_id, revision_no)``,
   ``char_length(body) BETWEEN 1 AND 5000``), ``provenance`` JSONB NOT NULL (D-055 evidence:
   ``{"kind": "human"}`` or ``{"kind": "ai_generated", ...}``).
4. ``approval_events`` — append-only audit of submit/approve/reject/withdraw with the actor
   tenant **and** user; ``note`` ≤ 1000 chars.

downgrade()
-----------
Drops the content tables (events → revisions → items) and the ``scopes_verified`` column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts(name: str) -> sa.Column:
    return sa.Column(
        name,
        postgresql.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def upgrade() -> None:
    # ---------------------------------------------------------------- A-1 ---
    op.add_column(
        "social_connections",
        sa.Column(
            "scopes_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    op.execute(
        "UPDATE social_connections SET scopes_verified = true "
        "WHERE granted_scopes IS NOT NULL AND granted_scopes::text <> '[]'"
    )

    # ----------------------------------------------------------------- B1 ---
    op.create_table(
        "content_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "brand_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("brands.id", ondelete="CASCADE", name="fk_content_items_brand_id"),
            nullable=False,
        ),
        sa.Column(
            "created_by_tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "tenants.id", ondelete="RESTRICT", name="fk_content_items_created_by_tenant_id"
            ),
            nullable=False,
        ),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("target_page_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        # FK to content_revisions is added after both tables exist (circular reference).
        sa.Column("current_revision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scheduled_for", postgresql.TIMESTAMP(timezone=True), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint(
            "channel IN ('facebook_page', 'instagram')", name="ck_content_items_channel"
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'pending_approval', 'approved', 'rejected', "
            "'scheduled', 'archived')",
            name="ck_content_items_status",
        ),
        sa.CheckConstraint(
            "(status = 'scheduled') = (scheduled_for IS NOT NULL)",
            name="ck_content_items_scheduled_for_iff_scheduled",
        ),
    )
    op.create_index("ix_content_items_brand_status", "content_items", ["brand_id", "status"])
    op.create_index(
        "ix_content_items_brand_scheduled_for",
        "content_items",
        ["brand_id", "scheduled_for"],
        postgresql_where=sa.text("status = 'scheduled'"),
    )

    op.create_table(
        "content_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "content_items.id", ondelete="CASCADE", name="fk_content_revisions_item_id"
            ),
            nullable=False,
        ),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "media_refs", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("provenance", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_by_tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "tenants.id",
                ondelete="RESTRICT",
                name="fk_content_revisions_created_by_tenant_id",
            ),
            nullable=False,
        ),
        _ts("created_at"),
        sa.UniqueConstraint("item_id", "revision_no", name="uq_content_revisions_item_no"),
        sa.CheckConstraint(
            "char_length(body) BETWEEN 1 AND 5000", name="ck_content_revisions_body_length"
        ),
    )
    op.create_foreign_key(
        "fk_content_items_current_revision_id",
        "content_items",
        "content_revisions",
        ["current_revision_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "approval_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "content_items.id", ondelete="CASCADE", name="fk_approval_events_item_id"
            ),
            nullable=False,
        ),
        sa.Column(
            "revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "content_revisions.id",
                ondelete="CASCADE",
                name="fk_approval_events_revision_id",
            ),
            nullable=False,
        ),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column(
            "actor_tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "tenants.id", ondelete="RESTRICT", name="fk_approval_events_actor_tenant_id"
            ),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "users.id", ondelete="RESTRICT", name="fk_approval_events_actor_user_id"
            ),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        _ts("created_at"),
        sa.CheckConstraint(
            "action IN ('submitted', 'approved', 'rejected', 'withdrawn')",
            name="ck_approval_events_action",
        ),
        sa.CheckConstraint(
            "note IS NULL OR char_length(note) <= 1000", name="ck_approval_events_note_length"
        ),
    )
    op.create_index(
        "ix_approval_events_item_created", "approval_events", ["item_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_approval_events_item_created", table_name="approval_events")
    op.drop_table("approval_events")
    op.drop_constraint("fk_content_items_current_revision_id", "content_items", type_="foreignkey")
    op.drop_table("content_revisions")
    op.drop_index("ix_content_items_brand_scheduled_for", table_name="content_items")
    op.drop_index("ix_content_items_brand_status", table_name="content_items")
    op.drop_table("content_items")
    op.drop_column("social_connections", "scopes_verified")
