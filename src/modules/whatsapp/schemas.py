"""Request / response models for ``whatsapp`` (SPRINT-09 B-2 / B-3).

Visibility table (§2-0 و): ``conversations.*``, ``messages.body`` and
``reply_drafts.suggested_body/status`` are visible to every brand role (VIEWER read-only);
the private draft provenance (provider / model / prompt_hash / tokens / latency) is
OWNER/MANAGER only; ``whatsapp_accounts.phone_number_id/display_phone/status`` OWNER/MANAGER;
the two encrypted credential columns of ``whatsapp_accounts`` never appear in any response —
this module has no field named after either of them (guarded by §3-7).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.modules.whatsapp.domain.models import (
    MAX_BODY_LENGTH,
    AccountStatus,
    Conversation,
    ConversationStatus,
    DeliveryStatus,
    Direction,
    DraftStatus,
    Message,
    MessageKind,
    RejectionReason,
    ReplyDraft,
    WhatsAppAccount,
)

MASKED_WA_ID = re.compile(r"^\.{3}\d{4}$")
PUBLIC_DRAFT_PROVENANCE_KEYS: frozenset[str] = frozenset(
    {"kind", "generated_at", "prompt_version", "truncated"}
)


def mask_wa_id(wa_id: str) -> str:
    """VIEWER sees ``...1234`` (three ASCII dots + last four digits — §2-0 و)."""
    digits = "".join(ch for ch in wa_id if ch.isdigit())
    return "..." + digits[-4:].rjust(4, "0")


def public_draft_provenance(provenance: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in provenance.items() if k in PUBLIC_DRAFT_PROVENANCE_KEYS}


# ------------------------------------------------------------------- account -----
class WhatsAppAccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone_number_id: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    waba_id: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    display_phone: str = Field(default="", max_length=32)
    system_token: str = Field(min_length=16, max_length=512, repr=False)
    app_secret: str = Field(min_length=8, max_length=256, repr=False)
    verify_token: str = Field(min_length=8, max_length=256, repr=False)


class WhatsAppAccountPublic(BaseModel):
    """OWNER/MANAGER view — no secret, no hash."""

    id: UUID
    brand_id: UUID
    phone_number_id: str
    waba_id: str
    display_phone: str
    status: AccountStatus
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, account: WhatsAppAccount) -> WhatsAppAccountPublic:
        return cls(
            id=account.id,
            brand_id=account.brand_id,
            phone_number_id=account.phone_number_id,
            waba_id=account.waba_id,
            display_phone=account.display_phone,
            status=account.status,
            created_at=account.created_at,
            updated_at=account.updated_at,
        )


# ------------------------------------------------------------- conversations -----
class ConversationPublic(BaseModel):
    id: UUID
    wa_id: str
    profile_name: str | None
    last_message_preview: str | None
    last_inbound_at: datetime | None
    last_outbound_at: datetime | None
    unread_count: int
    window_open: bool
    window_expires_at: datetime | None
    pending_draft: bool
    status: ConversationStatus

    @classmethod
    def from_domain(
        cls,
        conv: Conversation,
        *,
        preview: str | None,
        pending_draft: bool,
        window_open: bool,
        mask: bool,
    ) -> ConversationPublic:
        return cls(
            id=conv.id,
            wa_id=mask_wa_id(conv.wa_id) if mask else conv.wa_id,
            profile_name=conv.profile_name,
            last_message_preview=preview,
            last_inbound_at=conv.last_inbound_at,
            last_outbound_at=conv.last_outbound_at,
            unread_count=conv.unread_count,
            window_open=window_open,
            window_expires_at=conv.window_expires_at,
            pending_draft=pending_draft,
            status=conv.status,
        )


class ConversationPage(BaseModel):
    items: list[ConversationPublic]
    next_cursor: str | None


class ConversationStatePublic(BaseModel):
    id: UUID
    status: ConversationStatus
    unread_count: int
    updated_at: datetime

    @classmethod
    def from_domain(cls, conv: Conversation) -> ConversationStatePublic:
        return cls(
            id=conv.id,
            status=conv.status,
            unread_count=conv.unread_count,
            updated_at=conv.updated_at,
        )


# ------------------------------------------------------------------ messages -----
class MessagePublic(BaseModel):
    id: UUID
    direction: Direction
    kind: MessageKind
    body: str | None
    media_id: str | None
    template_name: str | None
    delivery_status: DeliveryStatus
    error_code: int | None
    sent_by_user_id: UUID | None
    reply_draft_id: UUID | None
    platform_timestamp: datetime | None
    created_at: datetime

    @classmethod
    def from_domain(cls, message: Message) -> MessagePublic:
        return cls(
            id=message.id,
            direction=message.direction,
            kind=message.kind,
            body=message.body,
            media_id=message.media_id,
            template_name=message.template_name,
            delivery_status=message.delivery_status,
            error_code=message.error_code,
            sent_by_user_id=message.sent_by_user_id,
            reply_draft_id=message.reply_draft_id,
            platform_timestamp=message.platform_timestamp,
            created_at=message.created_at,
        )


class ReplyDraftPublic(BaseModel):
    id: UUID
    in_reply_to_message_id: UUID
    suggested_body: str
    final_body: str | None
    status: DraftStatus
    provenance: dict[str, Any]
    decided_by_user_id: UUID | None
    decided_at: datetime | None
    rejection_reason: RejectionReason | None
    created_at: datetime

    @classmethod
    def from_domain(cls, draft: ReplyDraft, *, private: bool) -> ReplyDraftPublic:
        return cls(
            id=draft.id,
            in_reply_to_message_id=draft.in_reply_to_message_id,
            suggested_body=draft.suggested_body,
            final_body=draft.final_body,
            status=draft.status,
            provenance=dict(draft.provenance)
            if private
            else public_draft_provenance(draft.provenance),
            decided_by_user_id=draft.decided_by_user_id,
            decided_at=draft.decided_at,
            rejection_reason=draft.rejection_reason,
            created_at=draft.created_at,
        )


class MessagesPage(BaseModel):
    conversation: ConversationStatePublic
    items: list[MessagePublic]
    reply_drafts: list[ReplyDraftPublic]
    next_cursor: str | None


class SendTextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=MAX_BODY_LENGTH)


class SendTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9_]+$")
    language_code: str = Field(min_length=2, max_length=8, pattern=r"^[a-z]{2}(_[A-Z]{2})?$")
    components: list[dict[str, Any]] | None = Field(default=None, max_length=10)


class SendResultPublic(BaseModel):
    message: MessagePublic
    delivery_status: DeliveryStatus
    duplicate: bool = False


# ------------------------------------------------------------- reply drafts B-3 -----
class SuggestReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tone: Literal["friendly", "formal", "promotional"] = "friendly"
    language: Literal["ar", "en", "auto"] = "auto"


class ApproveDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    final_body: str | None = Field(default=None, min_length=1, max_length=MAX_BODY_LENGTH)


class RejectDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: RejectionReason


class ApproveResultPublic(BaseModel):
    draft: ReplyDraftPublic
    message: MessagePublic | None
    delivery_status: DeliveryStatus | None
