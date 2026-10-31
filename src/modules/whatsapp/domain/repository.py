"""Repository protocol for ``whatsapp`` — one request session, PostgreSQL only in V1."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from src.modules.whatsapp.domain.models import (
    AccountStatus,
    Conversation,
    ConversationStatus,
    DeliveryStatus,
    DraftStatus,
    LearningSignal,
    Message,
    RejectionReason,
    ReplyDraft,
    WhatsAppAccount,
)

Cursor = tuple[datetime, UUID]


class WhatsAppRepository(Protocol):
    # ------------------------------------------------------------- accounts ----
    async def create_account(self, account: WhatsAppAccount) -> WhatsAppAccount: ...

    async def get_account(self, brand_id: UUID) -> WhatsAppAccount | None: ...

    async def get_account_by_id(self, account_id: UUID) -> WhatsAppAccount | None: ...

    async def get_account_by_phone_number_id(
        self, phone_number_id: str
    ) -> WhatsAppAccount | None: ...

    async def list_active_accounts(self) -> list[WhatsAppAccount]: ...

    async def set_account_status(
        self, account_id: UUID, status: AccountStatus, *, now: datetime
    ) -> None: ...

    # -------------------------------------------------------- conversations ----
    async def get_conversation(
        self, brand_id: UUID, conversation_id: UUID
    ) -> Conversation | None: ...

    async def get_conversation_for_update(
        self, brand_id: UUID, conversation_id: UUID
    ) -> Conversation | None: ...

    async def upsert_conversation_inbound(
        self,
        *,
        brand_id: UUID,
        account_id: UUID,
        wa_id: str,
        profile_name: str | None,
        at: datetime,
        window_expires_at: datetime,
    ) -> Conversation:
        """Insert or update on ``(account_id, wa_id)``: ``last_inbound_at``,
        ``window_expires_at``, ``unread_count + 1``, reopen if archived."""
        ...

    async def list_conversations(
        self,
        brand_id: UUID,
        *,
        status: ConversationStatus | None,
        limit: int,
        cursor: Cursor | None,
    ) -> list[tuple[Conversation, str | None, bool]]:
        """``(conversation, last message body, has pending suggested draft)`` ordered by
        ``last_inbound_at DESC, id DESC``; returns ``limit + 1`` rows for keyset paging."""
        ...

    async def mark_conversation_read(self, conversation_id: UUID, *, now: datetime) -> None: ...

    async def mark_conversation_read_delta(self, conversation_id: UUID, delta: int) -> None:
        """Adjust ``unread_count`` by ``delta`` (floored at 0) — undoes the bump of a
        duplicate webhook delivery."""
        ...

    async def set_conversation_status(
        self, conversation_id: UUID, status: ConversationStatus, *, now: datetime
    ) -> None: ...

    async def touch_outbound(self, conversation_id: UUID, *, at: datetime) -> None: ...

    # ------------------------------------------------------------- messages ----
    async def insert_inbound_if_new(self, message: Message) -> bool:
        """``INSERT … ON CONFLICT (wamid) DO NOTHING`` ⇒ ``True`` when inserted."""
        ...

    async def insert_outbound(self, message: Message) -> Message: ...

    async def get_message(self, brand_id: UUID, message_id: UUID) -> Message | None: ...

    async def get_message_by_client_id(
        self, brand_id: UUID, client_message_id: UUID
    ) -> Message | None: ...

    async def last_inbound_wamid(self, conversation_id: UUID) -> str | None: ...

    async def last_message(self, conversation_id: UUID) -> Message | None: ...

    async def list_messages(
        self, conversation_id: UUID, *, limit: int, cursor: Cursor | None
    ) -> list[Message]:
        """Ascending ``(created_at, id)``; ``limit + 1`` rows."""
        ...

    async def recent_messages(self, conversation_id: UUID, *, limit: int) -> list[Message]:
        """Newest-first ``limit`` rows (prompt context)."""
        ...

    async def set_delivery_status(
        self,
        message_id: UUID,
        status: DeliveryStatus,
        *,
        wamid: str | None = None,
        error_code: int | None = None,
    ) -> None: ...

    async def update_status_by_wamid(
        self, wamid: str, status: DeliveryStatus, *, error_code: int | None
    ) -> bool: ...

    # --------------------------------------------------------- reply drafts ----
    async def create_draft(self, draft: ReplyDraft) -> ReplyDraft: ...

    async def get_draft(self, brand_id: UUID, draft_id: UUID) -> ReplyDraft | None: ...

    async def pending_draft(self, conversation_id: UUID) -> ReplyDraft | None: ...

    async def list_drafts(self, conversation_id: UUID) -> list[ReplyDraft]: ...

    async def decide_draft(
        self,
        draft_id: UUID,
        *,
        status: DraftStatus,
        final_body: str | None,
        decided_by_user_id: UUID | None,
        decided_at: datetime,
        rejection_reason: RejectionReason | None = None,
    ) -> None: ...

    async def expire_pending_drafts(
        self, conversation_id: UUID, *, now: datetime
    ) -> list[ReplyDraft]:
        """``suggested ⇒ expired`` for the conversation; returns the drafts that changed."""
        ...

    # ------------------------------------------------------------- signals ----
    async def add_signal(self, signal: LearningSignal) -> LearningSignal: ...

    async def ai_token_usage_since(self, brand_id: UUID, since: datetime) -> int:
        """Tokens recorded in ``wa_reply_drafts.provenance`` since ``since`` (B-3 budget)."""
        ...

    async def ai_token_usage_combined_since(self, brand_id: UUID, since: datetime) -> int:
        """B-3: the A-5 daily budget shared by both AI features — **one** ``UNION ALL`` query
        over ``content_revisions.provenance`` (``kind = ai_generated``) and
        ``wa_reply_drafts.provenance`` for the brand since ``since``."""
        ...

    async def learning_signals(self, brand_id: UUID) -> list[dict[str, Any]]: ...
