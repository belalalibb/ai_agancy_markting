"""SQLAlchemy ORM tables for ``whatsapp`` (PostgreSQL dialect, migration 0009)."""

from __future__ import annotations

import uuid
from datetime import datetime
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


def _ts(*, nullable: bool = False) -> Mapped[datetime]:
    return mapped_column(TIMESTAMP(timezone=True), nullable=nullable)


class WhatsAppAccountRow(Base):
    __tablename__ = "whatsapp_accounts"
    __table_args__ = (
        UniqueConstraint("phone_number_id", name="uq_whatsapp_accounts_phone_number_id"),
        UniqueConstraint("brand_id", name="uq_whatsapp_accounts_brand_id"),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_whatsapp_accounts_status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), nullable=False
    )
    phone_number_id: Mapped[str] = mapped_column(String(32), nullable=False)
    waba_id: Mapped[str] = mapped_column(String(32), nullable=False)
    display_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    encrypted_system_token: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_app_secret: Mapped[str] = mapped_column(Text, nullable=False)
    verify_token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class ConversationRow(Base):
    __tablename__ = "wa_conversations"
    __table_args__ = (
        UniqueConstraint("account_id", "wa_id", name="uq_wa_conversations_account_wa_id"),
        Index(
            "ix_wa_conversations_brand_status_last_inbound",
            "brand_id",
            "status",
            text("last_inbound_at DESC"),
        ),
        CheckConstraint("unread_count >= 0", name="ck_wa_conversations_unread_count"),
        CheckConstraint("status IN ('open', 'archived')", name="ck_wa_conversations_status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("whatsapp_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    wa_id: Mapped[str] = mapped_column(String(32), nullable=False)
    profile_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_inbound_at: Mapped[datetime | None] = _ts(nullable=True)
    last_outbound_at: Mapped[datetime | None] = _ts(nullable=True)
    window_expires_at: Mapped[datetime | None] = _ts(nullable=True)
    unread_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class MessageRow(Base):
    __tablename__ = "wa_messages"
    __table_args__ = (
        Index(
            "uq_wa_messages_wamid",
            "wamid",
            unique=True,
            postgresql_where=text("wamid IS NOT NULL"),
        ),
        Index(
            "uq_wa_messages_client_message_id",
            "client_message_id",
            unique=True,
            postgresql_where=text("client_message_id IS NOT NULL"),
        ),
        Index("ix_wa_messages_conversation_created", "conversation_id", "created_at"),
        CheckConstraint("direction IN ('inbound', 'outbound')", name="ck_wa_messages_direction"),
        CheckConstraint(
            "kind IN ('text', 'template', 'image', 'audio', 'video', 'document', 'sticker', "
            "'location', 'contacts', 'unsupported')",
            name="ck_wa_messages_kind",
        ),
        CheckConstraint(
            "delivery_status IN ('received', 'queued', 'sent', 'delivered', 'read', 'failed', "
            "'ambiguous')",
            name="ck_wa_messages_delivery_status",
        ),
        CheckConstraint("body IS NULL OR length(body) <= 4096", name="ck_wa_messages_body_length"),
        CheckConstraint(
            "direction <> 'outbound' OR sent_by_user_id IS NOT NULL",
            name="ck_wa_messages_outbound_has_actor",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wa_conversations.id", ondelete="CASCADE"), nullable=False
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), nullable=False
    )
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    wamid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    client_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    template_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    delivery_status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sent_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reply_draft_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wa_reply_drafts.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )
    platform_timestamp: Mapped[datetime | None] = _ts(nullable=True)
    created_at: Mapped[datetime] = _ts()


class ReplyDraftRow(Base):
    __tablename__ = "wa_reply_drafts"
    __table_args__ = (
        Index("ix_wa_reply_drafts_conversation_status", "conversation_id", "status"),
        CheckConstraint(
            "status IN ('suggested', 'approved', 'edited', 'rejected', 'expired')",
            name="ck_wa_reply_drafts_status",
        ),
        CheckConstraint(
            "rejection_reason IS NULL OR rejection_reason IN ('off_tone', 'factually_wrong', "
            "'too_long', 'inappropriate', 'other')",
            name="ck_wa_reply_drafts_rejection_reason",
        ),
        CheckConstraint(
            "status NOT IN ('approved', 'edited', 'rejected') OR decided_by_user_id IS NOT NULL",
            name="ck_wa_reply_drafts_decided_has_actor",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wa_conversations.id", ondelete="CASCADE"), nullable=False
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), nullable=False
    )
    in_reply_to_message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wa_messages.id", ondelete="CASCADE"), nullable=False
    )
    suggested_body: Mapped[str] = mapped_column(Text, nullable=False)
    final_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="suggested")
    provenance: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = _ts(nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = _ts()


class LearningSignalRow(Base):
    """D-069: no text column — guarded by ``test_b1_u8_learning_signals_have_no_text_column``."""

    __tablename__ = "wa_learning_signals"
    __table_args__ = (
        UniqueConstraint("reply_draft_id", name="uq_wa_learning_signals_reply_draft_id"),
        CheckConstraint(
            "signal IN ('approved_unchanged', 'approved_after_edit', 'rejected', 'expired')",
            name="ck_wa_learning_signals_signal",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), nullable=False
    )
    reply_draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wa_reply_drafts.id", ondelete="CASCADE"), nullable=False
    )
    signal: Mapped[str] = mapped_column(String(32), nullable=False)
    edit_distance_ratio: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    credential_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    features: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = _ts()
