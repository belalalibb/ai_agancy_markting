"""AI reply suggestion behind human approval (SPRINT-09 B-3, D-055 / D-069).

Flow: ``suggest`` ⇒ ``wa_reply_drafts(status=suggested)`` via the AI gateway — **no WhatsApp
call** — then a human ``approve`` (optionally editing the text) sends through the same B-2
path (:meth:`WhatsAppInboxService.send_text`) with ``messages.reply_draft_id`` linked, or
``reject``. Every decision writes one :class:`LearningSignal` made of **features only**
(:mod:`src.modules.whatsapp.signals`): the text never lands in the signals table.

Budget: the daily token budget is the one SPRINT-07 A-5 introduced
(``AI_DAILY_TOKEN_BUDGET_PER_BRAND``) read through the **single** shared reader of SPRINT-10
A-4 (:mod:`src.modules.ai_gateway.budget`), so every AI feature shares one allowance (N4).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from src.core.metrics import counters
from src.modules.ai_gateway.budget import BudgetUsageReader, DailyBudget
from src.modules.ai_gateway.gateway import AIGateway, CompletionRequest
from src.modules.auth.domain.rate_limit import RateLimiter
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
from src.modules.whatsapp.domain.repository import IndependentWrites, WhatsAppRepository
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
from src.modules.whatsapp.signals import language as detect_language

logger = logging.getLogger(__name__)

SUGGEST_BUCKET = "wa_suggest"
SUGGEST_LIMIT_PER_HOUR = 60
SUGGEST_WINDOW_SECONDS = 3600
SUGGEST_MAX_TOKENS = 400
SUGGEST_TEMPERATURE = 0.5


@dataclass(frozen=True, slots=True)
class ApproveOutcome:
    draft: ReplyDraft
    message: Message | None  # None when the window was closed (draft stays approved unsent)


class ReplySuggestionService:
    def __init__(
        self,
        repo: WhatsAppRepository,
        budget_reader: BudgetUsageReader,
        gateway: AIGateway,
        inbox: WhatsAppInboxService,
        limiter: RateLimiter,
        independent: IndependentWrites,
        *,
        daily_token_budget: int,
    ) -> None:
        self._repo = repo
        self._budget_reader = budget_reader
        self._gateway = gateway
        self._inbox = inbox
        self._limiter = limiter
        self._independent = independent  # SPRINT-10 A-1 (D-072)
        self._budget = DailyBudget(daily_token_budget)  # SPRINT-10 A-4 (N4)

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
        # A-6: ``auto`` is resolved server-side from the last inbound text (mixed/other ⇒ ar);
        # an explicit value always wins. Stored privately as ``language_detected``.
        language_detected = resolve_language(language, last.body)
        system = reply_system_prompt(brand_display_name, language_detected, tone)
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
            "language_detected": language_detected,
        }
        try:
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
        except IntegrityError as exc:
            # A-5: ``uq_wa_reply_drafts_one_pending`` (migration 0010) lost the race after
            # ``gateway.complete()`` — the tokens are counted, the draft is not stacked.
            counters.increment("wa_suggest_race_lost")
            raise DraftPendingError() from exc
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
        """A-5 budget shared by every AI feature through the one A-4 reader."""
        verdict = await self._budget.check(brand_id, usage_reader=self._budget_reader)
        if not verdict.allowed:
            raise GenerationBudgetExceededError(
                verdict.budget, verdict.used, verdict.resets_in_seconds
            )

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

    async def approve(
        self,
        brand_id: UUID,
        draft_id: UUID,
        *,
        actor_user_id: UUID,
        final_body: str | None,
    ) -> ApproveOutcome:
        """D-072 «ثبّت ثم أبلغ» (A-1): the decision (``approved``/``edited``), its learning
        signal and the counter are **committed in an independent transaction** before the
        B-2 send. A closed window then raises 422 carrying ``draft_status`` — the draft stays
        decided, no message, no Graph call."""
        draft = await self.get_pending_or_raise(brand_id, draft_id)
        now = datetime.now(tz=UTC)
        body = draft.suggested_body if final_body is None else final_body
        edited = body != draft.suggested_body

        async def _decide(repo: WhatsAppRepository) -> ReplyDraft:
            return await self._decide_and_record(
                repo, brand_id, draft, body=body, edited=edited, actor=actor_user_id, now=now
            )

        decided = await self._independent.run(_decide)
        counters.increment("wa_draft_approved")
        try:
            result = await self._inbox.send_text(
                brand_id,
                draft.conversation_id,
                body=body,
                actor_user_id=actor_user_id,
                reply_draft_id=draft.id,
            )
        except WindowClosedError as exc:
            logger.info("wa draft approved but window closed: draft=%s", draft.id)
            raise WindowClosedError(draft_status=decided.status.value) from exc
        return ApproveOutcome(draft=decided, message=result.message)

    @staticmethod
    async def _decide_and_record(
        repo: WhatsAppRepository,
        brand_id: UUID,
        draft: ReplyDraft,
        *,
        body: str,
        edited: bool,
        actor: UUID,
        now: datetime,
    ) -> ReplyDraft:
        """Runs on the independent session: lock ⇒ re-check ``suggested`` ⇒ decide ⇒ signal."""
        locked = await _lock_pending(repo, brand_id, draft.id)
        await repo.decide_draft(
            locked.id,
            status=DraftStatus.EDITED if edited else DraftStatus.APPROVED,
            final_body=body,
            decided_by_user_id=actor,
            decided_at=now,
        )
        decided = await _reload(repo, brand_id, locked.id)
        await repo.add_signal(
            build_signal(
                decided,
                Signal.APPROVED_AFTER_EDIT if edited else Signal.APPROVED_UNCHANGED,
                edit_distance_ratio=edit_distance_ratio(draft.suggested_body, body)
                if edited
                else 0.0,
                now=now,
            )
        )
        return decided

    async def reject(
        self, brand_id: UUID, draft_id: UUID, *, actor_user_id: UUID, reason: RejectionReason
    ) -> ReplyDraft:
        """``rejected`` + its signal, committed independently as well (D-072)."""
        draft = await self.get_pending_or_raise(brand_id, draft_id)
        now = datetime.now(tz=UTC)

        async def _reject(repo: WhatsAppRepository) -> ReplyDraft:
            locked = await _lock_pending(repo, brand_id, draft.id)
            await repo.decide_draft(
                locked.id,
                status=DraftStatus.REJECTED,
                final_body=None,
                decided_by_user_id=actor_user_id,
                decided_at=now,
                rejection_reason=reason,
            )
            decided = await _reload(repo, brand_id, locked.id)
            await repo.add_signal(build_signal(decided, Signal.REJECTED, now=now))
            return decided

        decided = await self._independent.run(_reject)
        counters.increment("wa_draft_rejected")
        return decided


async def _lock_pending(repo: WhatsAppRepository, brand_id: UUID, draft_id: UUID) -> ReplyDraft:
    locked = await repo.get_draft_for_update(brand_id, draft_id)
    if locked is None:
        raise DraftNotFoundError()
    if locked.status is not DraftStatus.SUGGESTED:
        raise DraftNotPendingError(locked.status.value)
    return locked


async def _reload(repo: WhatsAppRepository, brand_id: UUID, draft_id: UUID) -> ReplyDraft:
    draft = await repo.get_draft(brand_id, draft_id)
    assert draft is not None  # noqa: S101 - just decided under the row lock
    return draft


def resolve_language(language: Language, last_inbound_body: str | None) -> Language:
    """A-6: ``auto`` ⇒ :func:`signals.language` on the last inbound text; ``mixed`` /
    ``other`` (and no text) ⇒ ``ar``. Explicit ``ar``/``en`` are returned unchanged."""
    if language != "auto":
        return language
    detected = detect_language(last_inbound_body or "")
    return "en" if detected == "en" else "ar"
