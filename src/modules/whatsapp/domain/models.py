"""Domain models for ``whatsapp`` (frozen Pydantic — mirrors migration 0009)."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Final
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

WINDOW: Final[timedelta] = timedelta(hours=24)
MAX_BODY_LENGTH: Final[int] = 4096


class AccountStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class ConversationStatus(StrEnum):
    OPEN = "open"
    ARCHIVED = "archived"


class Direction(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class MessageKind(StrEnum):
    TEXT = "text"
    TEMPLATE = "template"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    DOCUMENT = "document"
    STICKER = "sticker"
    LOCATION = "location"
    CONTACTS = "contacts"
    UNSUPPORTED = "unsupported"


class DeliveryStatus(StrEnum):
    RECEIVED = "received"
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"


class DraftStatus(StrEnum):
    SUGGESTED = "suggested"
    APPROVED = "approved"
    EDITED = "edited"
    REJECTED = "rejected"
    EXPIRED = "expired"


class RejectionReason(StrEnum):
    OFF_TONE = "off_tone"
    FACTUALLY_WRONG = "factually_wrong"
    TOO_LONG = "too_long"
    INAPPROPRIATE = "inappropriate"
    OTHER = "other"


class Signal(StrEnum):
    APPROVED_UNCHANGED = "approved_unchanged"
    APPROVED_AFTER_EDIT = "approved_after_edit"
    REJECTED = "rejected"
    EXPIRED = "expired"


def message_kind_from_type(value: str | None) -> MessageKind:
    """Cloud API ``messages[].type`` ⇒ :class:`MessageKind` (unknown ⇒ ``unsupported``)."""
    try:
        kind = MessageKind(value or "")
    except ValueError:
        return MessageKind.UNSUPPORTED
    return MessageKind.UNSUPPORTED if kind is MessageKind.TEMPLATE else kind


class WhatsAppAccount(BaseModel):
    """One BYOK Cloud API number per brand. ``encrypted_*`` fields are ``repr=False``
    (D-064) and never serialised into any API response."""

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    brand_id: UUID
    phone_number_id: str = Field(max_length=32)
    waba_id: str = Field(max_length=32)
    display_phone: str = Field(max_length=32)
    encrypted_system_token: str = Field(repr=False, exclude=True)
    encrypted_app_secret: str = Field(repr=False, exclude=True)
    verify_token_hash: str = Field(repr=False, exclude=True)
    status: AccountStatus = AccountStatus.ACTIVE
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime


class Conversation(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    brand_id: UUID
    account_id: UUID
    wa_id: str = Field(max_length=32)
    profile_name: str | None = Field(default=None, max_length=128)
    last_inbound_at: datetime | None = None
    last_outbound_at: datetime | None = None
    window_expires_at: datetime | None = None
    unread_count: int = Field(default=0, ge=0)
    status: ConversationStatus = ConversationStatus.OPEN
    created_at: datetime
    updated_at: datetime

    def window_open(self, now: datetime) -> bool:
        return self.window_expires_at is not None and now < self.window_expires_at


class Message(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    conversation_id: UUID
    brand_id: UUID
    direction: Direction
    wamid: str | None = Field(default=None, max_length=128)
    client_message_id: UUID | None = None
    kind: MessageKind
    body: str | None = Field(default=None, max_length=MAX_BODY_LENGTH)
    media_id: str | None = Field(default=None, max_length=128)
    template_name: str | None = Field(default=None, max_length=128)
    delivery_status: DeliveryStatus
    error_code: int | None = None
    sent_by_user_id: UUID | None = None
    reply_draft_id: UUID | None = None
    platform_timestamp: datetime | None = None
    created_at: datetime


class ReplyDraft(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    conversation_id: UUID
    brand_id: UUID
    in_reply_to_message_id: UUID
    suggested_body: str
    final_body: str | None = None
    status: DraftStatus = DraftStatus.SUGGESTED
    provenance: dict[str, Any] = Field(default_factory=dict)
    decided_by_user_id: UUID | None = None
    decided_at: datetime | None = None
    rejection_reason: RejectionReason | None = None
    created_at: datetime


class LearningSignal(BaseModel):
    """D-069: features only — **no message text** is ever stored here."""

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    brand_id: UUID
    reply_draft_id: UUID
    signal: Signal
    edit_distance_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    rejection_reason: RejectionReason | None = None
    prompt_version: str = Field(max_length=16)
    model: str = Field(max_length=64)
    credential_mode: str = Field(max_length=16)
    features: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
