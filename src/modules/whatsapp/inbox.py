"""Unified inbox service (SPRINT-09 B-2): list / read / send / template / archive.

Every outbound message is written **before** the network call with ``delivery_status=
'queued'`` and a ``client_message_id`` (idempotency), then exactly one ``POST
…/messages`` is attempted (D-070):

* success ⇒ ``sent`` + ``wamid``;
* :class:`WhatsAppAmbiguousError` ⇒ ``ambiguous`` (202 upstream, never auto-retried);
* :class:`WhatsAppTransientError` ⇒ ``failed`` + 503 ``whatsapp_transient`` (``Retry-After``);
* :class:`WhatsAppRateLimitedError` ⇒ ``failed`` + 429;
* :class:`WhatsAppRecipientError` ⇒ ``failed`` + 422 ``whatsapp_recipient_rejected``;
* :class:`WhatsAppAuthError` ⇒ ``failed`` + account ``disabled`` + 409
  ``whatsapp_reauth_required``.

§2-0 (أ): ``sent_by_user_id`` is always the acting human; §2-0 (ج): the token is decrypted
per call and ``del``-ed in ``finally``; message text is never logged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from src.core.metrics import counters
from src.modules.whatsapp.client import WhatsAppCloudClient
from src.modules.whatsapp.domain.models import (
    MAX_BODY_LENGTH,
    AccountStatus,
    Conversation,
    ConversationStatus,
    DeliveryStatus,
    Direction,
    Message,
    MessageKind,
    ReplyDraft,
)
from src.modules.whatsapp.domain.repository import Cursor, WhatsAppRepository
from src.modules.whatsapp.exceptions import (
    AccountNotFoundError,
    ConversationNotFoundError,
    PermanentSendError,
    RateLimitedSendError,
    ReauthRequiredError,
    RecipientRejectedError,
    TransientSendError,
    WhatsAppAmbiguousError,
    WhatsAppAuthError,
    WhatsAppClientError,
    WhatsAppRateLimitedError,
    WhatsAppRecipientError,
    WhatsAppTransientError,
    WindowClosedError,
)
from src.modules.whatsapp.service import PostgresWhatsAppTokenProvider

logger = logging.getLogger(__name__)

PREVIEW_LENGTH = 80


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    conversation: Conversation
    last_message_preview: str | None
    pending_draft: bool
    window_open: bool


@dataclass(frozen=True, slots=True)
class SendResult:
    message: Message
    duplicate: bool = False


def preview(body: str | None) -> str | None:
    if body is None:
        return None
    flat = " ".join(body.split())
    return flat if len(flat) <= PREVIEW_LENGTH else flat[: PREVIEW_LENGTH - 1] + "…"


class WhatsAppInboxService:
    def __init__(
        self,
        repo: WhatsAppRepository,
        client: WhatsAppCloudClient,
        tokens: PostgresWhatsAppTokenProvider,
    ) -> None:
        self._repo = repo
        self._client = client
        self._tokens = tokens

    # ------------------------------------------------------------------ reads ----
    async def list_conversations(
        self,
        brand_id: UUID,
        *,
        status: ConversationStatus | None,
        limit: int,
        cursor: Cursor | None,
        now: datetime | None = None,
    ) -> list[ConversationSummary]:
        now = now or datetime.now(tz=UTC)
        rows = await self._repo.list_conversations(
            brand_id, status=status, limit=limit, cursor=cursor
        )
        return [
            ConversationSummary(
                conversation=conv,
                last_message_preview=preview(body),
                pending_draft=pending,
                window_open=conv.window_open(now),
            )
            for conv, body, pending in rows
        ]

    async def get_conversation(self, brand_id: UUID, conversation_id: UUID) -> Conversation:
        conv = await self._repo.get_conversation(brand_id, conversation_id)
        if conv is None:
            raise ConversationNotFoundError()
        return conv

    async def list_messages(
        self, brand_id: UUID, conversation_id: UUID, *, limit: int, cursor: Cursor | None
    ) -> tuple[Conversation, list[Message], list[ReplyDraft]]:
        conv = await self.get_conversation(brand_id, conversation_id)
        messages = await self._repo.list_messages(conversation_id, limit=limit, cursor=cursor)
        drafts = await self._repo.list_drafts(conversation_id)
        return conv, messages, drafts

    # ----------------------------------------------------------------- writes ----
    async def mark_read(self, brand_id: UUID, conversation_id: UUID) -> Conversation:
        """``unread_count = 0`` + best-effort ``mark_read`` to Meta (network failure never
        fails the request — the local state is what the inbox shows)."""
        conv = await self.get_conversation(brand_id, conversation_id)
        now = datetime.now(tz=UTC)
        await self._repo.mark_conversation_read(conv.id, now=now)
        wamid = await self._repo.last_inbound_wamid(conv.id)
        target = await self._tokens.resolve(conv.account_id)
        if wamid is not None and target is not None:
            token = target.token
            try:
                await self._client.mark_read(token, target.phone_number_id, wamid)
            except WhatsAppClientError as exc:
                logger.info(
                    "wa mark_read ignored failure: conv=%s error=%s", conv.id, type(exc).__name__
                )
            finally:
                del token
        return conv.model_copy(update={"unread_count": 0, "updated_at": now})

    async def archive(self, brand_id: UUID, conversation_id: UUID) -> Conversation:
        conv = await self.get_conversation(brand_id, conversation_id)
        now = datetime.now(tz=UTC)
        await self._repo.set_conversation_status(conv.id, ConversationStatus.ARCHIVED, now=now)
        return conv.model_copy(update={"status": ConversationStatus.ARCHIVED, "updated_at": now})

    async def send_text(
        self,
        brand_id: UUID,
        conversation_id: UUID,
        *,
        body: str,
        actor_user_id: UUID,
        client_message_id: UUID | None = None,
        reply_draft_id: UUID | None = None,
    ) -> SendResult:
        """Free-form text — only inside the 24-hour window (else 422 ``whatsapp_window_closed``
        with ``template_required``)."""
        if len(body) > MAX_BODY_LENGTH:
            raise PermanentSendError(None)
        if client_message_id is not None:
            existing = await self._repo.get_message_by_client_id(brand_id, client_message_id)
            if existing is not None:
                return SendResult(existing, duplicate=True)
        conv = await self._lock_conversation(brand_id, conversation_id)
        now = datetime.now(tz=UTC)
        if not conv.window_open(now):
            raise WindowClosedError()
        message = Message(
            conversation_id=conv.id,
            brand_id=brand_id,
            direction=Direction.OUTBOUND,
            client_message_id=client_message_id,
            kind=MessageKind.TEXT,
            body=body,
            delivery_status=DeliveryStatus.QUEUED,
            sent_by_user_id=actor_user_id,
            reply_draft_id=reply_draft_id,
            created_at=now,
        )
        await self._repo.insert_outbound(message)

        async def _call(token: str, phone_number_id: str) -> str:
            return (await self._client.send_text(token, phone_number_id, conv.wa_id, body)).wamid

        return SendResult(await self._deliver(conv, message, _call))

    async def send_template(
        self,
        brand_id: UUID,
        conversation_id: UUID,
        *,
        name: str,
        language_code: str,
        components: list[dict[str, Any]] | None,
        actor_user_id: UUID,
        client_message_id: UUID | None = None,
    ) -> SendResult:
        """Approved template — allowed inside *and* outside the window."""
        if client_message_id is not None:
            existing = await self._repo.get_message_by_client_id(brand_id, client_message_id)
            if existing is not None:
                return SendResult(existing, duplicate=True)
        conv = await self._lock_conversation(brand_id, conversation_id)
        now = datetime.now(tz=UTC)
        message = Message(
            conversation_id=conv.id,
            brand_id=brand_id,
            direction=Direction.OUTBOUND,
            client_message_id=client_message_id,
            kind=MessageKind.TEMPLATE,
            template_name=name,
            delivery_status=DeliveryStatus.QUEUED,
            sent_by_user_id=actor_user_id,
            created_at=now,
        )
        await self._repo.insert_outbound(message)

        async def _call(token: str, phone_number_id: str) -> str:
            sent = await self._client.send_template(
                token, phone_number_id, conv.wa_id, name, language_code, components
            )
            return sent.wamid

        return SendResult(await self._deliver(conv, message, _call))

    # ---------------------------------------------------------------- helpers ----
    async def _lock_conversation(self, brand_id: UUID, conversation_id: UUID) -> Conversation:
        conv = await self._repo.get_conversation_for_update(brand_id, conversation_id)
        if conv is None:
            raise ConversationNotFoundError()
        return conv

    async def _deliver(self, conv: Conversation, message: Message, call) -> Message:  # noqa: ANN001
        target = await self._tokens.resolve(conv.account_id)
        if target is None:
            await self._repo.set_delivery_status(message.id, DeliveryStatus.FAILED)
            raise AccountNotFoundError()
        now = datetime.now(tz=UTC)
        token = target.token
        try:
            wamid = await call(token, target.phone_number_id)
        except WhatsAppAmbiguousError as exc:
            await self._repo.set_delivery_status(
                message.id, DeliveryStatus.AMBIGUOUS, error_code=exc.code
            )
            await self._repo.touch_outbound(conv.id, at=now)
            counters.increment("wa_send_ambiguous")
            self._log(conv, message, "ambiguous")
            # Pending suggestions are stale once a human replied (even ambiguously).
            await self._repo.expire_pending_drafts(conv.id, now=now)
            return message.model_copy(
                update={"delivery_status": DeliveryStatus.AMBIGUOUS, "error_code": exc.code}
            )
        except WhatsAppClientError as exc:
            await self._repo.set_delivery_status(
                message.id, DeliveryStatus.FAILED, error_code=exc.code
            )
            counters.increment("wa_send_failed")
            self._log(conv, message, f"failed:{type(exc).__name__}")
            raise self._to_service_error(conv, exc, now) from exc
        finally:
            del token
        await self._repo.set_delivery_status(message.id, DeliveryStatus.SENT, wamid=wamid)
        await self._repo.touch_outbound(conv.id, at=now)
        await self._repo.expire_pending_drafts(conv.id, now=now)
        counters.increment("wa_sent")
        self._log(conv, message, "sent")
        return message.model_copy(update={"delivery_status": DeliveryStatus.SENT, "wamid": wamid})

    def _to_service_error(
        self, conv: Conversation, exc: WhatsAppClientError, now: datetime
    ) -> Exception:
        if isinstance(exc, WhatsAppRateLimitedError):
            return RateLimitedSendError(exc.retry_after_seconds)
        if isinstance(exc, WhatsAppRecipientError):
            return RecipientRejectedError(exc.code)
        if isinstance(exc, WhatsAppTransientError):
            return TransientSendError()
        if isinstance(exc, WhatsAppAuthError):
            return ReauthRequiredError()
        return PermanentSendError(exc.code)

    async def disable_account_after_auth_failure(self, conv: Conversation) -> None:
        await self._repo.set_account_status(
            conv.account_id, AccountStatus.DISABLED, now=datetime.now(tz=UTC)
        )

    @staticmethod
    def _log(conv: Conversation, message: Message, status: str) -> None:
        logger.info(
            "wa send: brand=%s conv=%s kind=%s len=%d status=%s",
            conv.brand_id,
            conv.id,
            message.kind.value,
            len(message.body or ""),
            status,
        )
