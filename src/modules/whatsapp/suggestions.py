"""AI reply suggestion behind human approval (SPRINT-09 B-3, D-055 / D-069).

Flow: ``suggest`` ⇒ ``wa_reply_drafts(status=suggested)`` via the AI gateway — **no WhatsApp
call** — then a human ``approve`` (optionally editing the text) sends through the same B-2
path (:meth:`WhatsAppInboxService.send_text`) with ``messages.reply_draft_id`` linked, or
``reject``. Every decision writes one :class:`LearningSignal` made of **features only**
(:mod:`src.modules.whatsapp.signals`): the text never lands in the signals table.

Budget: the daily token budget is the one SPRINT-07 A-5 introduced
(``AI_DAILY_TOKEN_BUDGET_PER_BRAND``), summed over ``content_revisions.provenance`` **and**
``wa_reply_drafts.provenance`` together, so the two AI features share one allowance.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from src.core.metrics import counters
from src.modules.ai_gateway.gateway import AIGateway, CompletionRequest
from src.modules.auth.domain.rate_limit import RateLimiter
from src.modules.content.domain.repository import ContentRepository
from src.modules.content.exceptions import (
    GenerationBudgetExceededError,
    GenerationRateLimitedError,
)
from src.modules.whatsapp.domain.models import (
    Direction,
    DraftStatus,
    Message,
    RejectionReason,
    ReplyDraft,
    Signal,
)
from src.modules.whatsapp.domain.repository import WhatsAppRepository
from src.modules.whatsapp.exceptions import (
    ConversationNotFoundError,
    DraftNotFoundError,
    DraftNotPendingError,
    DraftPendingError,
    NoInboundToReplyError,
    WindowClosedError,
)
from src.modules.whatsapp.inbox import WhatsAppInboxService
from src.modules.whatsapp.learning import build_signal
from src.modules.whatsapp.prompts import (
    DEFAULT_MAX_TURNS,
    REPLY_MAX_CHARS,
    REPLY_PROMPT_VERSION,
    Language,
    Tone,
    prompt_hash,
    reply_system_prompt,
    reply_user_prompt,
)
from src.modules.whatsapp.signals import edit_distance_ratio

logger = logging.getLogger(__name__)

SUGGEST_BUCKET = "wa_suggest"
SUGGEST_LIMIT_PER_HOUR = 60
SUGGEST_WINDOW_SECONDS = 3600
SUGGEST_MAX_TOKENS = 400
SUGGEST_TEMPERATURE = 0.5
TOKEN_BUDGET_WINDOW = timedelta(hours=24)  # same window as content A-5


@dataclass(frozen=True, slots=True)
class ApproveOutcome:
    draft: ReplyDraft
    message: Message | None  # None when the window was closed (draft stays approved unsent)


class ReplySuggestionService:
    def __init__(
        self,
        repo: WhatsAppRepository,
        content_repo: ContentRepository,
        gateway: AIGateway,
        inbox: WhatsAppInboxService,
        limiter: RateLimiter,
        *,
        daily_token_budget: int,
    ) -> None:
        self._repo = repo
        self._content_repo = content_repo
        self._gateway = gateway
        self._inbox = inbox
        self._limiter = limiter
        self._daily_token_budget = daily_token_budget

    # ---------------------------------------------------------------- suggest ----
    async def suggest(
        self,
        brand_id: UUID,
        conversation_id: UUID,
        *,
        actor_tenant_id: UUID,
        brand_display_name: str,
        tone: Tone,
        language: Language,
    ) -> ReplyDraft:
        conv = await self._repo.get_conversation(brand_id, conversation_id)
        if conv is None:
            raise ConversationNotFoundError()
        last = await self._repo.last_message(conv.id)
        if last is None or last.direction is not Direction.INBOUND:
            raise NoInboundToReplyError()
        if await self._repo.pending_draft(conv.id) is not None:
            raise DraftPendingError()
        retry_after = self._limiter.hit(
            SUGGEST_BUCKET, str(brand_id), SUGGEST_LIMIT_PER_HOUR, SUGGEST_WINDOW_SECONDS
        )
        if retry_after is not None:
            raise GenerationRateLimitedError(retry_after)
        await self._check_token_budget(brand_id)

        recent = await self._repo.recent_messages(conv.id, limit=DEFAULT_MAX_TURNS)
        transcript = [(m.direction.value, m.body) for m in reversed(recent)]
        system = reply_system_prompt(brand_display_name, language, tone)
        user = reply_user_prompt(transcript, max_turns=DEFAULT_MAX_TURNS)
        request = CompletionRequest(
            purpose="reply_suggestion",
            system=system,
            user=user,
            max_tokens=SUGGEST_MAX_TOKENS,
            temperature=SUGGEST_TEMPERATURE,
            brand_id=brand_id,
            tenant_id=actor_tenant_id,
        )
        try:
            result = await self._gateway.complete(request)
        except Exception:
            counters.increment("ai_failures")
            raise
        text = result.text.strip()
        truncated = len(text) > REPLY_MAX_CHARS
        if truncated:
            text = text[:REPLY_MAX_CHARS].rstrip()
        now = datetime.now(tz=UTC)
        provenance: dict[str, Any] = {
            "kind": "ai_suggested",
            "generated_at": now.isoformat(),
            "prompt_version": REPLY_PROMPT_VERSION,
            "truncated": truncated,
            # private keys (OWNER/MANAGER only — VIEWER gets public_draft_provenance()):
            "provider": result.provider,
            "model": result.model,
            "credential_mode": result.credential_mode,
            "prompt_hash": prompt_hash(system, user),
            "prompt_tokens": result.usage.prompt_tokens,
            "completion_tokens": result.usage.completion_tokens,
            "latency_ms": result.latency_ms,
            "finish_reason": result.finish_reason,
            "tone": tone,
            "language": language,
        }
        draft = await self._repo.create_draft(
            ReplyDraft(
                conversation_id=conv.id,
                brand_id=brand_id,
                in_reply_to_message_id=last.id,
                suggested_body=text or "…",
                provenance=provenance,
                created_at=now,
            )
        )
        counters.increment("wa_suggestions")
        counters.increment("ai_completions")
        logger.info(
            "wa suggestion: brand=%s conv=%s draft=%s provider=%s model=%s prompt_tokens=%s "
            "completion_tokens=%s latency_ms=%d len=%d",
            brand_id,
            conv.id,
            draft.id,
            result.provider,
            result.model,
            result.usage.prompt_tokens,
            result.usage.completion_tokens,
            result.latency_ms,
            len(draft.suggested_body),
        )
        return draft

    async def _check_token_budget(self, brand_id: UUID) -> None:
        """A-5 budget shared by both AI features: content revisions + reply drafts."""
        now = datetime.now(tz=UTC)
        since = now - TOKEN_BUDGET_WINDOW
        used = await self._repo.ai_token_usage_combined_since(brand_id, since)  # one UNION ALL
        if used < self._daily_token_budget:
            return
        oldest = await self._content_repo.oldest_ai_generation_at(brand_id, since)
        resets_in = (
            max(int((oldest + TOKEN_BUDGET_WINDOW - now).total_seconds()), 1)
            if oldest is not None
            else int(TOKEN_BUDGET_WINDOW.total_seconds())
        )
        raise GenerationBudgetExceededError(self._daily_token_budget, used, resets_in)

    # ---------------------------------------------------------------- decide -----
    async def get_pending_or_raise(self, brand_id: UUID, draft_id: UUID) -> ReplyDraft:
        """The ``suggested`` draft — else 404 ``whatsapp_draft_not_found`` or 409
        ``draft_not_pending``."""
        draft = await self._repo.get_draft(brand_id, draft_id)
        if draft is None:
            raise DraftNotFoundError()
        if draft.status is not DraftStatus.SUGGESTED:
            raise DraftNotPendingError(draft.status.value)
        return draft

    async def _reload(self, brand_id: UUID, draft_id: UUID) -> ReplyDraft:
        draft = await self._repo.get_draft(brand_id, draft_id)
        assert draft is not None  # noqa: S101 - just decided under the same session
        return draft

    async def approve(
        self,
        brand_id: UUID,
        draft_id: UUID,
        *,
        actor_user_id: UUID,
        final_body: str | None,
    ) -> ApproveOutcome:
        """``approved`` (+``approved_unchanged``) or ``edited`` (+``approved_after_edit``),
        **then** the B-2 send. A closed window raises 422 after the decision is persisted
        (the draft stays approved, no message, no Graph call)."""
        draft = await self.get_pending_or_raise(brand_id, draft_id)
        now = datetime.now(tz=UTC)
        body = draft.suggested_body if final_body is None else final_body
        edited = body != draft.suggested_body
        await self._repo.decide_draft(
            draft.id,
            status=DraftStatus.EDITED if edited else DraftStatus.APPROVED,
            final_body=body,
            decided_by_user_id=actor_user_id,
            decided_at=now,
        )
        decided = await self._reload(brand_id, draft.id)
        await self._repo.add_signal(
            build_signal(
                decided,
                Signal.APPROVED_AFTER_EDIT if edited else Signal.APPROVED_UNCHANGED,
                edit_distance_ratio=edit_distance_ratio(draft.suggested_body, body)
                if edited
                else 0.0,
                now=now,
            )
        )
        counters.increment("wa_draft_approved")
        try:
            result = await self._inbox.send_text(
                brand_id,
                draft.conversation_id,
                body=body,
                actor_user_id=actor_user_id,
                reply_draft_id=draft.id,
            )
        except WindowClosedError:
            logger.info("wa draft approved but window closed: draft=%s", draft.id)
            raise
        return ApproveOutcome(draft=decided, message=result.message)

    async def reject(
        self, brand_id: UUID, draft_id: UUID, *, actor_user_id: UUID, reason: RejectionReason
    ) -> ReplyDraft:
        draft = await self.get_pending_or_raise(brand_id, draft_id)
        now = datetime.now(tz=UTC)
        await self._repo.decide_draft(
            draft.id,
            status=DraftStatus.REJECTED,
            final_body=None,
            decided_by_user_id=actor_user_id,
            decided_at=now,
            rejection_reason=reason,
        )
        decided = await self._reload(brand_id, draft.id)
        await self._repo.add_signal(build_signal(decided, Signal.REJECTED, now=now))
        counters.increment("wa_draft_rejected")
        return decided
