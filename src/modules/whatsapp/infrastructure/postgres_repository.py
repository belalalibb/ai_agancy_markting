"""PostgreSQL implementation of :class:`WhatsAppRepository` (migration 0009)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Integer, and_, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.whatsapp.domain.models import (
    AccountStatus,
    Conversation,
    ConversationStatus,
    DeliveryStatus,
    Direction,
    DraftStatus,
    LearningSignal,
    Message,
    MessageKind,
    RejectionReason,
    ReplyDraft,
    WhatsAppAccount,
)
from src.modules.whatsapp.domain.repository import Cursor
from src.modules.whatsapp.infrastructure.orm import (
    ConversationRow,
    LearningSignalRow,
    MessageRow,
    ReplyDraftRow,
    WhatsAppAccountRow,
)


def _account(row: WhatsAppAccountRow) -> WhatsAppAccount:
    return WhatsAppAccount(
        id=row.id,
        brand_id=row.brand_id,
        phone_number_id=row.phone_number_id,
        waba_id=row.waba_id,
        display_phone=row.display_phone,
        encrypted_system_token=row.encrypted_system_token,
        encrypted_app_secret=row.encrypted_app_secret,
        verify_token_hash=row.verify_token_hash,
        status=AccountStatus(row.status),
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _conversation(row: ConversationRow) -> Conversation:
    return Conversation(
        id=row.id,
        brand_id=row.brand_id,
        account_id=row.account_id,
        wa_id=row.wa_id,
        profile_name=row.profile_name,
        last_inbound_at=row.last_inbound_at,
        last_outbound_at=row.last_outbound_at,
        window_expires_at=row.window_expires_at,
        unread_count=row.unread_count,
        status=ConversationStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _message(row: MessageRow) -> Message:
    return Message(
        id=row.id,
        conversation_id=row.conversation_id,
        brand_id=row.brand_id,
        direction=Direction(row.direction),
        wamid=row.wamid,
        client_message_id=row.client_message_id,
        kind=MessageKind(row.kind),
        body=row.body,
        media_id=row.media_id,
        template_name=row.template_name,
        delivery_status=DeliveryStatus(row.delivery_status),
        error_code=row.error_code,
        sent_by_user_id=row.sent_by_user_id,
        reply_draft_id=row.reply_draft_id,
        platform_timestamp=row.platform_timestamp,
        created_at=row.created_at,
    )


def _draft(row: ReplyDraftRow) -> ReplyDraft:
    return ReplyDraft(
        id=row.id,
        conversation_id=row.conversation_id,
        brand_id=row.brand_id,
        in_reply_to_message_id=row.in_reply_to_message_id,
        suggested_body=row.suggested_body,
        final_body=row.final_body,
        status=DraftStatus(row.status),
        provenance=dict(row.provenance or {}),
        decided_by_user_id=row.decided_by_user_id,
        decided_at=row.decided_at,
        rejection_reason=RejectionReason(row.rejection_reason) if row.rejection_reason else None,
        created_at=row.created_at,
    )


class PostgresWhatsAppRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------- accounts ----
    async def create_account(self, account: WhatsAppAccount) -> WhatsAppAccount:
        self._session.add(
            WhatsAppAccountRow(
                id=account.id,
                brand_id=account.brand_id,
                phone_number_id=account.phone_number_id,
                waba_id=account.waba_id,
                display_phone=account.display_phone,
                encrypted_system_token=account.encrypted_system_token,
                encrypted_app_secret=account.encrypted_app_secret,
                verify_token_hash=account.verify_token_hash,
                status=account.status.value,
                created_by_user_id=account.created_by_user_id,
                created_at=account.created_at,
                updated_at=account.updated_at,
            )
        )
        await self._session.flush()
        return account

    async def get_account(self, brand_id: UUID) -> WhatsAppAccount | None:
        row = await self._session.scalar(
            select(WhatsAppAccountRow).where(WhatsAppAccountRow.brand_id == brand_id)
        )
        return _account(row) if row is not None else None

    async def get_account_by_id(self, account_id: UUID) -> WhatsAppAccount | None:
        row = await self._session.get(WhatsAppAccountRow, account_id)
        return _account(row) if row is not None else None

    async def get_account_by_phone_number_id(self, phone_number_id: str) -> WhatsAppAccount | None:
        row = await self._session.scalar(
            select(WhatsAppAccountRow).where(WhatsAppAccountRow.phone_number_id == phone_number_id)
        )
        return _account(row) if row is not None else None

    async def list_active_accounts(self) -> list[WhatsAppAccount]:
        rows = await self._session.scalars(
            select(WhatsAppAccountRow).where(
                WhatsAppAccountRow.status == AccountStatus.ACTIVE.value
            )
        )
        return [_account(r) for r in rows]

    async def set_account_status(
        self, account_id: UUID, status: AccountStatus, *, now: datetime
    ) -> None:
        await self._session.execute(
            update(WhatsAppAccountRow)
            .where(WhatsAppAccountRow.id == account_id)
            .values(status=status.value, updated_at=now)
        )

    # -------------------------------------------------------- conversations ----
    async def get_conversation(self, brand_id: UUID, conversation_id: UUID) -> Conversation | None:
        row = await self._session.scalar(
            select(ConversationRow).where(
                ConversationRow.brand_id == brand_id, ConversationRow.id == conversation_id
            )
        )
        return _conversation(row) if row is not None else None

    async def get_conversation_for_update(
        self, brand_id: UUID, conversation_id: UUID
    ) -> Conversation | None:
        row = await self._session.scalar(
            select(ConversationRow)
            .where(ConversationRow.brand_id == brand_id, ConversationRow.id == conversation_id)
            .with_for_update()
        )
        return _conversation(row) if row is not None else None

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
        fresh_id = Conversation(
            brand_id=brand_id,
            account_id=account_id,
            wa_id=wa_id,
            created_at=at,
            updated_at=at,
        ).id
        stmt = pg_insert(ConversationRow).values(
            id=fresh_id,
            brand_id=brand_id,
            account_id=account_id,
            wa_id=wa_id,
            profile_name=profile_name,
            last_inbound_at=at,
            window_expires_at=window_expires_at,
            unread_count=1,
            status=ConversationStatus.OPEN.value,
            created_at=at,
            updated_at=at,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_wa_conversations_account_wa_id",
            set_={
                "profile_name": func.coalesce(
                    stmt.excluded.profile_name, ConversationRow.profile_name
                ),
                # Out-of-order webhook delivery: never move the window backwards.
                "last_inbound_at": func.greatest(ConversationRow.last_inbound_at, at),
                "window_expires_at": func.greatest(
                    ConversationRow.window_expires_at, window_expires_at
                ),
                "unread_count": ConversationRow.unread_count + 1,
                "status": ConversationStatus.OPEN.value,  # archived ⇒ reopen
                "updated_at": at,
            },
        ).returning(ConversationRow)
        row = (await self._session.execute(stmt)).scalar_one()
        return _conversation(row)

    async def list_conversations(
        self,
        brand_id: UUID,
        *,
        status: ConversationStatus | None,
        limit: int,
        cursor: Cursor | None,
    ) -> list[tuple[Conversation, str | None, bool]]:
        last_body = (
            select(MessageRow.body)
            .where(MessageRow.conversation_id == ConversationRow.id)
            .order_by(MessageRow.created_at.desc(), MessageRow.id.desc())
            .limit(1)
            .correlate(ConversationRow)
            .scalar_subquery()
        )
        pending = exists(
            select(ReplyDraftRow.id).where(
                ReplyDraftRow.conversation_id == ConversationRow.id,
                ReplyDraftRow.status == DraftStatus.SUGGESTED.value,
            )
        )
        stmt = select(ConversationRow, last_body, pending).where(
            ConversationRow.brand_id == brand_id
        )
        if status is not None:
            stmt = stmt.where(ConversationRow.status == status.value)
        if cursor is not None:
            last_at, last_id = cursor
            stmt = stmt.where(
                or_(
                    ConversationRow.last_inbound_at < last_at,
                    and_(ConversationRow.last_inbound_at == last_at, ConversationRow.id < last_id),
                )
            )
        stmt = stmt.order_by(
            ConversationRow.last_inbound_at.desc().nulls_last(), ConversationRow.id.desc()
        ).limit(limit + 1)
        rows = (await self._session.execute(stmt)).all()
        return [(_conversation(row), body, bool(flag)) for row, body, flag in rows]

    async def mark_conversation_read(self, conversation_id: UUID, *, now: datetime) -> None:
        await self._session.execute(
            update(ConversationRow)
            .where(ConversationRow.id == conversation_id)
            .values(unread_count=0, updated_at=now)
        )

    async def mark_conversation_read_delta(self, conversation_id: UUID, delta: int) -> None:
        await self._session.execute(
            update(ConversationRow)
            .where(ConversationRow.id == conversation_id)
            .values(unread_count=func.greatest(ConversationRow.unread_count + delta, 0))
        )

    async def set_conversation_status(
        self, conversation_id: UUID, status: ConversationStatus, *, now: datetime
    ) -> None:
        await self._session.execute(
            update(ConversationRow)
            .where(ConversationRow.id == conversation_id)
            .values(status=status.value, updated_at=now)
        )

    async def touch_outbound(self, conversation_id: UUID, *, at: datetime) -> None:
        await self._session.execute(
            update(ConversationRow)
            .where(ConversationRow.id == conversation_id)
            .values(last_outbound_at=at, updated_at=at)
        )

    # ------------------------------------------------------------- messages ----
    @staticmethod
    def _row_values(message: Message) -> dict[str, Any]:
        return {
            "id": message.id,
            "conversation_id": message.conversation_id,
            "brand_id": message.brand_id,
            "direction": message.direction.value,
            "wamid": message.wamid,
            "client_message_id": message.client_message_id,
            "kind": message.kind.value,
            "body": message.body,
            "media_id": message.media_id,
            "template_name": message.template_name,
            "delivery_status": message.delivery_status.value,
            "error_code": message.error_code,
            "sent_by_user_id": message.sent_by_user_id,
            "reply_draft_id": message.reply_draft_id,
            "platform_timestamp": message.platform_timestamp,
            "created_at": message.created_at,
        }

    async def insert_inbound_if_new(self, message: Message) -> bool:
        stmt = (
            pg_insert(MessageRow)
            .values(**self._row_values(message))
            .on_conflict_do_nothing(
                index_elements=[MessageRow.wamid], index_where=MessageRow.wamid.isnot(None)
            )
            .returning(MessageRow.id)
        )
        inserted = (await self._session.execute(stmt)).scalar_one_or_none()
        return inserted is not None

    async def insert_outbound(self, message: Message) -> Message:
        self._session.add(MessageRow(**self._row_values(message)))
        await self._session.flush()
        return message

    async def get_message(self, brand_id: UUID, message_id: UUID) -> Message | None:
        row = await self._session.scalar(
            select(MessageRow).where(MessageRow.brand_id == brand_id, MessageRow.id == message_id)
        )
        return _message(row) if row is not None else None

    async def get_message_by_client_id(
        self, brand_id: UUID, client_message_id: UUID
    ) -> Message | None:
        row = await self._session.scalar(
            select(MessageRow).where(
                MessageRow.brand_id == brand_id, MessageRow.client_message_id == client_message_id
            )
        )
        return _message(row) if row is not None else None

    async def last_inbound_wamid(self, conversation_id: UUID) -> str | None:
        return await self._session.scalar(
            select(MessageRow.wamid)
            .where(
                MessageRow.conversation_id == conversation_id,
                MessageRow.direction == Direction.INBOUND.value,
                MessageRow.wamid.isnot(None),
            )
            .order_by(MessageRow.created_at.desc(), MessageRow.id.desc())
            .limit(1)
        )

    async def last_message(self, conversation_id: UUID) -> Message | None:
        row = await self._session.scalar(
            select(MessageRow)
            .where(MessageRow.conversation_id == conversation_id)
            .order_by(MessageRow.created_at.desc(), MessageRow.id.desc())
            .limit(1)
        )
        return _message(row) if row is not None else None

    async def list_messages(
        self, conversation_id: UUID, *, limit: int, cursor: Cursor | None
    ) -> list[Message]:
        stmt = select(MessageRow).where(MessageRow.conversation_id == conversation_id)
        if cursor is not None:
            last_at, last_id = cursor
            stmt = stmt.where(
                or_(
                    MessageRow.created_at > last_at,
                    and_(MessageRow.created_at == last_at, MessageRow.id > last_id),
                )
            )
        stmt = stmt.order_by(MessageRow.created_at.asc(), MessageRow.id.asc()).limit(limit + 1)
        return [_message(r) for r in await self._session.scalars(stmt)]

    async def recent_messages(self, conversation_id: UUID, *, limit: int) -> list[Message]:
        rows = await self._session.scalars(
            select(MessageRow)
            .where(MessageRow.conversation_id == conversation_id)
            .order_by(MessageRow.created_at.desc(), MessageRow.id.desc())
            .limit(limit)
        )
        return [_message(r) for r in rows]

    async def set_delivery_status(
        self,
        message_id: UUID,
        status: DeliveryStatus,
        *,
        wamid: str | None = None,
        error_code: int | None = None,
    ) -> None:
        values: dict[str, Any] = {"delivery_status": status.value}
        if wamid is not None:
            values["wamid"] = wamid
        if error_code is not None:
            values["error_code"] = error_code
        await self._session.execute(
            update(MessageRow).where(MessageRow.id == message_id).values(**values)
        )

    async def update_status_by_wamid(
        self, wamid: str, status: DeliveryStatus, *, error_code: int | None
    ) -> bool:
        values: dict[str, Any] = {"delivery_status": status.value}
        if error_code is not None:
            values["error_code"] = error_code
        result = await self._session.execute(
            update(MessageRow)
            .where(MessageRow.wamid == wamid, MessageRow.direction == Direction.OUTBOUND.value)
            .values(**values)
        )
        return (result.rowcount or 0) > 0

    # --------------------------------------------------------- reply drafts ----
    async def create_draft(self, draft: ReplyDraft) -> ReplyDraft:
        self._session.add(
            ReplyDraftRow(
                id=draft.id,
                conversation_id=draft.conversation_id,
                brand_id=draft.brand_id,
                in_reply_to_message_id=draft.in_reply_to_message_id,
                suggested_body=draft.suggested_body,
                final_body=draft.final_body,
                status=draft.status.value,
                provenance=dict(draft.provenance),
                decided_by_user_id=draft.decided_by_user_id,
                decided_at=draft.decided_at,
                rejection_reason=draft.rejection_reason.value if draft.rejection_reason else None,
                created_at=draft.created_at,
            )
        )
        await self._session.flush()
        return draft

    async def get_draft(self, brand_id: UUID, draft_id: UUID) -> ReplyDraft | None:
        row = await self._session.scalar(
            select(ReplyDraftRow)
            .where(ReplyDraftRow.brand_id == brand_id, ReplyDraftRow.id == draft_id)
            .with_for_update()
        )
        return _draft(row) if row is not None else None

    async def pending_draft(self, conversation_id: UUID) -> ReplyDraft | None:
        row = await self._session.scalar(
            select(ReplyDraftRow)
            .where(
                ReplyDraftRow.conversation_id == conversation_id,
                ReplyDraftRow.status == DraftStatus.SUGGESTED.value,
            )
            .order_by(ReplyDraftRow.created_at.desc())
            .limit(1)
        )
        return _draft(row) if row is not None else None

    async def list_drafts(self, conversation_id: UUID) -> list[ReplyDraft]:
        rows = await self._session.scalars(
            select(ReplyDraftRow)
            .where(ReplyDraftRow.conversation_id == conversation_id)
            .order_by(ReplyDraftRow.created_at.asc(), ReplyDraftRow.id.asc())
        )
        return [_draft(r) for r in rows]

    async def decide_draft(
        self,
        draft_id: UUID,
        *,
        status: DraftStatus,
        final_body: str | None,
        decided_by_user_id: UUID | None,
        decided_at: datetime,
        rejection_reason: RejectionReason | None = None,
    ) -> None:
        await self._session.execute(
            update(ReplyDraftRow)
            .where(ReplyDraftRow.id == draft_id)
            .values(
                status=status.value,
                final_body=final_body,
                decided_by_user_id=decided_by_user_id,
                decided_at=decided_at,
                rejection_reason=rejection_reason.value if rejection_reason else None,
            )
        )

    async def expire_pending_drafts(
        self, conversation_id: UUID, *, now: datetime
    ) -> list[ReplyDraft]:
        rows = (
            await self._session.execute(
                update(ReplyDraftRow)
                .where(
                    ReplyDraftRow.conversation_id == conversation_id,
                    ReplyDraftRow.status == DraftStatus.SUGGESTED.value,
                )
                .values(status=DraftStatus.EXPIRED.value, decided_at=now)
                .returning(ReplyDraftRow)
            )
        ).scalars()
        return [_draft(r) for r in rows]

    # ------------------------------------------------------------- signals ----
    async def add_signal(self, signal: LearningSignal) -> LearningSignal:
        self._session.add(
            LearningSignalRow(
                id=signal.id,
                brand_id=signal.brand_id,
                reply_draft_id=signal.reply_draft_id,
                signal=signal.signal.value,
                edit_distance_ratio=signal.edit_distance_ratio,
                rejection_reason=signal.rejection_reason.value
                if signal.rejection_reason
                else None,
                prompt_version=signal.prompt_version,
                model=signal.model,
                credential_mode=signal.credential_mode,
                features=dict(signal.features),
                created_at=signal.created_at,
            )
        )
        await self._session.flush()
        return signal

    async def ai_token_usage_since(self, brand_id: UUID, since: datetime) -> int:
        prov = ReplyDraftRow.provenance
        total = func.coalesce(
            func.sum(
                func.coalesce(prov["prompt_tokens"].astext.cast(Integer), 0)
                + func.coalesce(prov["completion_tokens"].astext.cast(Integer), 0)
            ),
            0,
        )
        value = await self._session.scalar(
            select(total).where(
                ReplyDraftRow.brand_id == brand_id, ReplyDraftRow.created_at >= since
            )
        )
        return int(value or 0)

    async def learning_signals(self, brand_id: UUID) -> list[dict[str, Any]]:
        rows = await self._session.scalars(
            select(LearningSignalRow)
            .where(LearningSignalRow.brand_id == brand_id)
            .order_by(LearningSignalRow.created_at.asc())
        )
        return [
            {
                "signal": r.signal,
                "edit_distance_ratio": (
                    float(r.edit_distance_ratio) if r.edit_distance_ratio is not None else None
                ),
                "rejection_reason": r.rejection_reason,
                "features": dict(r.features or {}),
            }
            for r in rows
        ]
