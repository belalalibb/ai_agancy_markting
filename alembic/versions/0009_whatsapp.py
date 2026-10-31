"""whatsapp — Cloud API accounts, conversations, messages, reply drafts, learning signals

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-31

SPRINT-09 Part B-1 (M6 WhatsApp Cloud API). Five tables, nothing else.

* ``whatsapp_accounts`` — one BYOK account per brand (V1: ``UNIQUE (brand_id)``); system
  token and app secret stored **encrypted** (D-064), ``verify_token_hash`` is a SHA-256.
* ``wa_conversations`` — one row per ``(account_id, wa_id)`` with the 24-hour customer
  service window (``window_expires_at = last_inbound_at + 24h``).
* ``wa_messages`` — inbound / outbound; ``wamid`` deduplicated by a partial unique index;
  CHECK: every outbound row carries ``sent_by_user_id`` (§2-0 أ — a human actor).
* ``wa_reply_drafts`` — AI suggestions behind human approval (D-055); the provenance is
  JSONB (private fields), decided rows carry ``decided_by_user_id``.
* ``wa_learning_signals`` — D-069: **no message text column** (features only).

downgrade() drops the five tables in reverse dependency order.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACCOUNT_STATUSES = "'active', 'disabled'"
CONVERSATION_STATUSES = "'open', 'archived'"
DIRECTIONS = "'inbound', 'outbound'"
KINDS = (
    "'text', 'template', 'image', 'audio', 'video', 'document', 'sticker', 'location', "
    "'contacts', 'unsupported'"
)
DELIVERY_STATUSES = "'received', 'queued', 'sent', 'delivered', 'read', 'failed', 'ambiguous'"
DRAFT_STATUSES = "'suggested', 'approved', 'edited', 'rejected', 'expired'"
REJECTION_REASONS = "'off_tone', 'factually_wrong', 'too_long', 'inappropriate', 'other'"
SIGNALS = "'approved_unchanged', 'approved_after_edit', 'rejected', 'expired'"


def _uuid(name: str, *, nullable: bool = False, primary_key: bool = False) -> sa.Column:
    return sa.Column(
        name, postgresql.UUID(as_uuid=True), nullable=nullable, primary_key=primary_key
    )


def _ts(name: str, *, nullable: bool = False, default_now: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.text("now()") if default_now else None,
    )


def upgrade() -> None:
    op.create_table(
        "whatsapp_accounts",
        _uuid("id", primary_key=True),
        sa.Column(
            "brand_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("brands.id", ondelete="CASCADE", name="fk_whatsapp_accounts_brand_id"),
            nullable=False,
        ),
        sa.Column("phone_number_id", sa.String(32), nullable=False),
        sa.Column("waba_id", sa.String(32), nullable=False),
        sa.Column("display_phone", sa.String(32), nullable=False),
        sa.Column("encrypted_system_token", sa.Text(), nullable=False),
        sa.Column("encrypted_app_secret", sa.Text(), nullable=False),
        sa.Column("verify_token_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        _uuid("created_by_user_id"),
        _ts("created_at", default_now=True),
        _ts("updated_at", default_now=True),
        sa.UniqueConstraint("phone_number_id", name="uq_whatsapp_accounts_phone_number_id"),
        sa.UniqueConstraint("brand_id", name="uq_whatsapp_accounts_brand_id"),
        sa.CheckConstraint(f"status IN ({ACCOUNT_STATUSES})", name="ck_whatsapp_accounts_status"),
    )

    op.create_table(
        "wa_conversations",
        _uuid("id", primary_key=True),
        sa.Column(
            "brand_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("brands.id", ondelete="CASCADE", name="fk_wa_conversations_brand_id"),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "whatsapp_accounts.id", ondelete="CASCADE", name="fk_wa_conversations_account_id"
            ),
            nullable=False,
        ),
        sa.Column("wa_id", sa.String(32), nullable=False),
        sa.Column("profile_name", sa.String(128), nullable=True),
        _ts("last_inbound_at", nullable=True),
        _ts("last_outbound_at", nullable=True),
        _ts("window_expires_at", nullable=True),
        sa.Column("unread_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        _ts("created_at", default_now=True),
        _ts("updated_at", default_now=True),
        sa.UniqueConstraint("account_id", "wa_id", name="uq_wa_conversations_account_wa_id"),
        sa.CheckConstraint("unread_count >= 0", name="ck_wa_conversations_unread_count"),
        sa.CheckConstraint(
            f"status IN ({CONVERSATION_STATUSES})", name="ck_wa_conversations_status"
        ),
    )
    op.create_index(
        "ix_wa_conversations_brand_status_last_inbound",
        "wa_conversations",
        ["brand_id", "status", sa.text("last_inbound_at DESC")],
    )

    op.create_table(
        "wa_messages",
        _uuid("id", primary_key=True),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "wa_conversations.id", ondelete="CASCADE", name="fk_wa_messages_conversation_id"
            ),
            nullable=False,
        ),
        sa.Column(
            "brand_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("brands.id", ondelete="CASCADE", name="fk_wa_messages_brand_id"),
            nullable=False,
        ),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("wamid", sa.String(128), nullable=True),
        _uuid("client_message_id", nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("media_id", sa.String(128), nullable=True),
        sa.Column("template_name", sa.String(128), nullable=True),
        sa.Column("delivery_status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.Integer(), nullable=True),
        _uuid("sent_by_user_id", nullable=True),
        _uuid("reply_draft_id", nullable=True),  # FK added after wa_reply_drafts exists
        _ts("platform_timestamp", nullable=True),
        _ts("created_at", default_now=True),
        sa.CheckConstraint(f"direction IN ({DIRECTIONS})", name="ck_wa_messages_direction"),
        sa.CheckConstraint(f"kind IN ({KINDS})", name="ck_wa_messages_kind"),
        sa.CheckConstraint(
            f"delivery_status IN ({DELIVERY_STATUSES})", name="ck_wa_messages_delivery_status"
        ),
        sa.CheckConstraint(
            "body IS NULL OR length(body) <= 4096", name="ck_wa_messages_body_length"
        ),
        # §2-0 (أ): no outbound message without a human actor.
        sa.CheckConstraint(
            "direction <> 'outbound' OR sent_by_user_id IS NOT NULL",
            name="ck_wa_messages_outbound_has_actor",
        ),
    )
    op.create_index(
        "uq_wa_messages_wamid",
        "wa_messages",
        ["wamid"],
        unique=True,
        postgresql_where=sa.text("wamid IS NOT NULL"),
    )
    op.create_index(
        "uq_wa_messages_client_message_id",
        "wa_messages",
        ["client_message_id"],
        unique=True,
        postgresql_where=sa.text("client_message_id IS NOT NULL"),
    )
    op.create_index(
        "ix_wa_messages_conversation_created",
        "wa_messages",
        ["conversation_id", "created_at"],
    )

    op.create_table(
        "wa_reply_drafts",
        _uuid("id", primary_key=True),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "wa_conversations.id",
                ondelete="CASCADE",
                name="fk_wa_reply_drafts_conversation_id",
            ),
            nullable=False,
        ),
        sa.Column(
            "brand_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("brands.id", ondelete="CASCADE", name="fk_wa_reply_drafts_brand_id"),
            nullable=False,
        ),
        sa.Column(
            "in_reply_to_message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "wa_messages.id", ondelete="CASCADE", name="fk_wa_reply_drafts_in_reply_to"
            ),
            nullable=False,
        ),
        sa.Column("suggested_body", sa.Text(), nullable=False),
        sa.Column("final_body", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="suggested"),
        sa.Column(
            "provenance", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        _uuid("decided_by_user_id", nullable=True),
        _ts("decided_at", nullable=True),
        sa.Column("rejection_reason", sa.String(32), nullable=True),
        _ts("created_at", default_now=True),
        sa.CheckConstraint(f"status IN ({DRAFT_STATUSES})", name="ck_wa_reply_drafts_status"),
        sa.CheckConstraint(
            f"rejection_reason IS NULL OR rejection_reason IN ({REJECTION_REASONS})",
            name="ck_wa_reply_drafts_rejection_reason",
        ),
        sa.CheckConstraint(
            "status NOT IN ('approved', 'edited', 'rejected') OR decided_by_user_id IS NOT NULL",
            name="ck_wa_reply_drafts_decided_has_actor",
        ),
    )
    op.create_index(
        "ix_wa_reply_drafts_conversation_status",
        "wa_reply_drafts",
        ["conversation_id", "status"],
    )
    op.create_foreign_key(
        "fk_wa_messages_reply_draft_id",
        "wa_messages",
        "wa_reply_drafts",
        ["reply_draft_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "wa_learning_signals",
        _uuid("id", primary_key=True),
        sa.Column(
            "brand_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("brands.id", ondelete="CASCADE", name="fk_wa_learning_signals_brand_id"),
            nullable=False,
        ),
        sa.Column(
            "reply_draft_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "wa_reply_drafts.id",
                ondelete="CASCADE",
                name="fk_wa_learning_signals_reply_draft_id",
            ),
            nullable=False,
        ),
        sa.Column("signal", sa.String(32), nullable=False),
        sa.Column("edit_distance_ratio", sa.Numeric(4, 3), nullable=True),
        sa.Column("rejection_reason", sa.String(32), nullable=True),
        sa.Column("prompt_version", sa.String(16), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("credential_mode", sa.String(16), nullable=False),
        sa.Column(
            "features", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        _ts("created_at", default_now=True),
        sa.UniqueConstraint("reply_draft_id", name="uq_wa_learning_signals_reply_draft_id"),
        sa.CheckConstraint(f"signal IN ({SIGNALS})", name="ck_wa_learning_signals_signal"),
    )


def downgrade() -> None:
    op.drop_table("wa_learning_signals")
    op.drop_constraint("fk_wa_messages_reply_draft_id", "wa_messages", type_="foreignkey")
    op.drop_table("wa_reply_drafts")
    op.drop_table("wa_messages")
    op.drop_table("wa_conversations")
    op.drop_table("whatsapp_accounts")
