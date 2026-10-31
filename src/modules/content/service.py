"""Use-case layer of ``content`` (SPRINT-06 B2/B3, SPRINT-07 B2).

All status changes go: pure :func:`transition` ⇒ ``compare_and_set_status`` (optimistic;
``False`` ⇒ ``InvalidTransitionError`` 409 because somebody else moved the item first) ⇒
``ApprovalEvent`` where the action is part of the approval loop.

SPRINT-07 B2: ``schedule`` also creates the ``publish_jobs`` row **in the same transaction**
as the status CAS (the request session commits both or neither); leaving ``scheduled`` cancels
the ``queued`` job in the same transaction, or refuses with 409 ``publish_in_progress`` when a
worker already leased it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from src.core.metrics import counters
from src.modules.ai_gateway.gateway import AIGateway, CompletionRequest
from src.modules.auth.domain.rate_limit import RateLimiter
from src.modules.content.domain.lifecycle import (
    EDIT_FORBIDDEN,
    EDIT_RESETS_TO_DRAFT,
    Action,
    LifecycleError,
    transition,
)
from src.modules.content.domain.models import (
    ApprovalAction,
    ApprovalEvent,
    Channel,
    ContentItem,
    ContentRevision,
    ContentStatus,
)
from src.modules.content.domain.repository import ContentRepository
from src.modules.content.exceptions import (
    ApprovalRequiresOwnerError,
    BodyTooLongError,
    ChannelNotActivatedError,
    ContentInsufficientRoleError,
    ContentNotFoundError,
    GenerationBudgetExceededError,
    GenerationRateLimitedError,
    InstagramNotLinkedError,
    InstagramRequiresImageError,
    InvalidTransitionError,
    ManualVerificationRequiredError,
    NoPublishJobError,
    PublishInProgressError,
    ReauthRequiredError,
    RejectNoteRequiredError,
    ScheduleTooSoonError,
    TargetPageNotSelectedError,
)
from src.modules.content.prompts import (
    PROMPT_VERSION,
    Language,
    Tone,
    prompt_hash,
    system_prompt,
    user_prompt,
)
from src.modules.publishing.channels import (
    ChannelDefinition,
    MediaRuleError,
    get_registry,
    missing_publish_scopes,
)
from src.modules.publishing.domain.models import (
    ErrorClass,
    JobStatus,
    PublishAttempt,
    PublishJob,
)
from src.modules.publishing.domain.repository import PublishJobRepository
from src.modules.publishing.exceptions import ActiveJobExistsError
from src.modules.social_connect.domain.models import AccountStatus, ConnectedAccount, PageAsset
from src.modules.social_connect.domain.repository import ConnectedAccountRepository
from src.modules.tenancy.domain.access import BrandRole

logger = logging.getLogger(__name__)

SCHEDULE_MIN_LEAD = timedelta(minutes=5)
REJECT_NOTE_MIN_CHARS = 3
REVISIONS_IN_DETAIL = 20
# B5: generations per brand per hour (existing ``RateLimiter`` bucket; 429 when exceeded).
GENERATION_BUCKET = "ai_generate_brand"
GENERATION_LIMIT_PER_HOUR = 20
GENERATION_WINDOW_SECONDS = 3600
GENERATION_STATES = frozenset({ContentStatus.DRAFT, ContentStatus.REJECTED})
# A-5: daily token budget window (per brand; summed from revision provenance).
TOKEN_BUDGET_WINDOW = timedelta(hours=24)
# Actions that take the item out of ``scheduled`` by hand (the job must be cancelled).
_CANCELLING_ACTIONS = frozenset({Action.UNSCHEDULE, Action.ARCHIVE})


def channel_definition(channel: Channel) -> ChannelDefinition:
    """SPRINT-08 B-1: every channel fact (limits, scopes, media rule, activation) is read
    from the publishing channel registry — ``content/`` owns no channel constants."""
    return get_registry().get(channel)


def enforce_channel_limit(channel: Channel, body: str) -> None:
    """A-2: 422 ``body_too_long`` when ``body`` exceeds the channel's hard limit."""
    limit = channel_definition(channel).limits
    if len(body) > limit:
        raise BodyTooLongError(channel, limit, len(body))


_EVENT_FOR_ACTION: dict[Action, ApprovalAction] = {
    Action.SUBMIT: ApprovalAction.SUBMITTED,
    Action.APPROVE: ApprovalAction.APPROVED,
    Action.REJECT: ApprovalAction.REJECTED,
    Action.WITHDRAW: ApprovalAction.WITHDRAWN,
    Action.SCHEDULE: ApprovalAction.SCHEDULED,
}


class ContentService:
    def __init__(
        self,
        repository: ContentRepository,
        connections: ConnectedAccountRepository,
        publish_jobs: PublishJobRepository,
        *,
        daily_token_budget: int = 200_000,
        publish_max_attempts: int = 3,
    ) -> None:
        self._repo = repository
        self._connections = connections
        self._jobs = publish_jobs
        self._daily_token_budget = daily_token_budget
        # SPRINT-08 A-3: written into every new job's ``max_attempts`` (policy at creation).
        self._publish_max_attempts = publish_max_attempts

    # ------------------------------------------------------------ create ---
    async def create_item(
        self,
        brand_id: UUID,
        *,
        actor_tenant_id: UUID,
        channel: Channel,
        body: str,
        target_page_id: str | None,
        media_refs: list[dict[str, Any]] | None = None,
    ) -> ContentItem:
        """Draft + revision 1 with ``provenance={"kind": "human"}``."""
        enforce_channel_limit(channel, body)
        now = datetime.now(tz=UTC)
        item = ContentItem(
            brand_id=brand_id,
            created_by_tenant_id=actor_tenant_id,
            channel=channel,
            target_page_id=target_page_id,
            status=ContentStatus.DRAFT,
            created_at=now,
            updated_at=now,
        )
        revision = ContentRevision(
            item_id=item.id,
            revision_no=1,
            body=body,
            media_refs=list(media_refs or []),
            provenance={"kind": "human"},
            created_by_tenant_id=actor_tenant_id,
            created_at=now,
        )
        stored = await self._repo.create_item(item, revision)
        logger.info(
            "content item created: brand=%s item=%s channel=%s", brand_id, item.id, channel.value
        )
        return stored

    # -------------------------------------------------------------- reads ---
    async def get_item(self, brand_id: UUID, item_id: UUID) -> ContentItem:
        item = await self._repo.get_item(brand_id, item_id)
        if item is None:
            raise ContentNotFoundError()
        return item

    async def get_detail(
        self, brand_id: UUID, item_id: UUID
    ) -> tuple[ContentItem, list[ContentRevision], list[ApprovalEvent]]:
        item = await self.get_item(brand_id, item_id)
        revisions = await self._repo.list_revisions(item_id, limit=REVISIONS_IN_DETAIL)
        events = await self._repo.list_events(item_id)
        return item, revisions, events

    async def list_revisions(self, item_id: UUID, *, limit: int) -> list[ContentRevision]:
        return await self._repo.list_revisions(item_id, limit=limit)

    async def list_items(
        self,
        brand_id: UUID,
        *,
        status: ContentStatus | None,
        limit: int,
        cursor: tuple[datetime, UUID] | None,
    ) -> tuple[list[tuple[ContentItem, str | None]], bool]:
        """A-4: ``(item, current_body)`` pairs from a single query."""
        rows = await self._repo.list_items(brand_id, status=status, limit=limit, cursor=cursor)
        has_more = len(rows) > limit
        return rows[:limit], has_more

    # ---------------------------------------------------------- body edit ---
    async def update_body(
        self,
        brand_id: UUID,
        item_id: UUID,
        *,
        body: str,
        actor_tenant_id: UUID,
        actor_user_id: UUID,
        provenance: dict[str, Any] | None = None,
        media_refs: list[dict[str, Any]] | None = None,
    ) -> ContentItem:
        """New revision. From ``pending_approval``/``approved``/``rejected``/``scheduled``/
        ``publish_failed`` the item returns to ``draft`` with an automatic ``withdrawn`` event
        (B2-U2). ``archived`` and ``published`` items are immutable ⇒ 409
        ``invalid_transition``. A-3: the item row is locked (``get_for_update``) before the
        revision number is assigned. From ``scheduled`` the queued publish job is cancelled
        too (409 ``publish_in_progress`` while a worker holds it)."""
        item = await self._lock_item(brand_id, item_id)
        if item.status in EDIT_FORBIDDEN:
            raise InvalidTransitionError(item.status, "edit")
        enforce_channel_limit(item.channel, body)
        now = datetime.now(tz=UTC)
        if item.status in EDIT_RESETS_TO_DRAFT:
            if item.status is ContentStatus.SCHEDULED:
                await self._cancel_active_job(item, now)
            moved = await self._repo.compare_and_set_status(
                item.id,
                expected=item.status,
                new=ContentStatus.DRAFT,
                scheduled_for=None,
                updated_at=now,
            )
            if not moved:
                raise InvalidTransitionError(item.status, "edit", "item changed concurrently")
            revision = await self._repo.add_revision(
                item.id,
                body=body,
                provenance=provenance or {"kind": "human"},
                created_by_tenant_id=actor_tenant_id,
                created_at=now,
                media_refs=media_refs,
            )
            await self._repo.add_event(
                ApprovalEvent(
                    item_id=item.id,
                    revision_id=revision.id,
                    action=ApprovalAction.WITHDRAWN,
                    actor_tenant_id=actor_tenant_id,
                    actor_user_id=actor_user_id,
                    note="body edited — returned to draft",
                    created_at=now,
                )
            )
        else:
            await self._repo.add_revision(
                item.id,
                body=body,
                provenance=provenance or {"kind": "human"},
                created_by_tenant_id=actor_tenant_id,
                created_at=now,
                media_refs=media_refs,
            )
        return await self.get_item(brand_id, item_id)

    async def _cancel_active_job(self, item: ContentItem, now: datetime) -> JobStatus | None:
        """B2: ``queued → cancelled`` for the item's publish job in the current transaction.
        A ``leased`` job belongs to a worker right now ⇒ 409 and **nothing** changes."""
        previous = await self._jobs.cancel_queued_job(item.id, now=now)
        if previous is JobStatus.LEASED:
            raise PublishInProgressError()
        return previous

    async def _lock_item(self, brand_id: UUID, item_id: UUID) -> ContentItem:
        item = await self._repo.get_for_update(brand_id, item_id)
        if item is None:
            raise ContentNotFoundError()
        return item

    # ------------------------------------------------------ B7 publish status ---
    async def publish_status(
        self, brand_id: UUID, item_id: UUID
    ) -> tuple[PublishJob, list[PublishAttempt]]:
        """The active job if any, else the latest one, with its attempts (oldest first).
        404 ``no_publish_job`` when the item was never scheduled."""
        await self.get_item(brand_id, item_id)
        job = await self._jobs.latest_job_for_item(item_id)
        if job is None:
            raise NoPublishJobError()
        return job, await self._jobs.list_attempts(job.id)

    async def retry_publish(
        self,
        brand_id: UUID,
        item_id: UUID,
        *,
        actor_role: BrandRole,
        actor_tenant_id: UUID,
        actor_user_id: UUID,
    ) -> ContentItem:
        """B7: ``publish_failed → scheduled`` + a new ``queued`` job with ``run_at = now`` +
        event ``scheduled`` (the new authorisation record, D-055). Re-validates the target
        exactly like ``schedule`` (connection active, scopes, IG image). An **ambiguous** last
        outcome ⇒ 409 ``manual_verification_required`` (the post may already be live)."""
        item = await self._lock_item(brand_id, item_id)
        now = datetime.now(tz=UTC)
        if actor_role not in {BrandRole.OWNER, BrandRole.MANAGER}:
            raise ContentInsufficientRoleError("only OWNER/MANAGER may retry a publish")
        if item.status is not ContentStatus.PUBLISH_FAILED:
            raise InvalidTransitionError(item.status, "publish_retry", "not publish_failed")
        last = await self._jobs.latest_job_for_item(item.id)
        if last is not None and last.last_error_class is ErrorClass.AMBIGUOUS_OUTCOME:
            raise ManualVerificationRequiredError()
        page = await self._validate_target(item, item.target_page_id)
        moved = await self._repo.compare_and_set_status(
            item.id,
            expected=ContentStatus.PUBLISH_FAILED,
            new=ContentStatus.SCHEDULED,
            scheduled_for=now,
            updated_at=now,
        )
        if not moved:
            raise InvalidTransitionError(item.status, "publish_retry", "item changed concurrently")
        scheduled = item.model_copy(
            update={"status": ContentStatus.SCHEDULED, "scheduled_for": now, "updated_at": now}
        )
        try:
            await self._jobs.create_job(
                _job_for(
                    item,
                    scheduled,
                    page,
                    actor_tenant_id,
                    actor_user_id,
                    now,
                    max_attempts=self._publish_max_attempts,
                )
            )
        except ActiveJobExistsError as exc:
            raise PublishInProgressError() from exc
        assert item.current_revision_id is not None  # noqa: S101 - publish_failed ⇒ has one
        await self._repo.add_event(
            ApprovalEvent(
                item_id=item.id,
                revision_id=item.current_revision_id,
                action=ApprovalAction.SCHEDULED,
                actor_tenant_id=actor_tenant_id,
                actor_user_id=actor_user_id,
                note="publish_retry",
                created_at=now,
            )
        )
        logger.info("content retry_publish: item=%s brand=%s", item.id, brand_id)
        return scheduled

    # --------------------------------------------------------- transitions ---
    async def act(
        self,
        brand_id: UUID,
        item_id: UUID,
        action: Action,
        *,
        actor_role: BrandRole,
        actor_tenant_id: UUID,
        actor_user_id: UUID,
        note: str | None = None,
        scheduled_for: datetime | None = None,
        target_page_id: str | None = None,
    ) -> ContentItem:
        item = await self.get_item(brand_id, item_id)
        now = datetime.now(tz=UTC)
        page: PageAsset | None = None
        if action is Action.REJECT and (note is None or len(note.strip()) < REJECT_NOTE_MIN_CHARS):
            # Role first (a MANAGER gets 403, not 422), then the note rule.
            _raise_if_error(
                transition(
                    item,
                    action,
                    actor_role=actor_role,
                    actor_tenant_id=actor_tenant_id,
                    now=now,
                )
            )
            raise RejectNoteRequiredError()
        if action is Action.SCHEDULE:
            # Role + state before the (repository-backed) schedule checks.
            _raise_if_error(
                transition(
                    item,
                    action,
                    actor_role=actor_role,
                    actor_tenant_id=actor_tenant_id,
                    now=now,
                    scheduled_for=scheduled_for,
                )
            )
            page_id = target_page_id or item.target_page_id
            page = await self._validate_schedule(item, scheduled_for, page_id, now)
            if page_id != item.target_page_id:
                await self._repo.set_target_page(item.id, page_id)
        if action in _CANCELLING_ACTIONS and item.status is ContentStatus.SCHEDULED:
            # Role + state first (a VIEWER gets 403, not 409), then the job.
            _raise_if_error(
                transition(
                    item, action, actor_role=actor_role, actor_tenant_id=actor_tenant_id, now=now
                )
            )
            await self._cancel_active_job(item, now)
        result = transition(
            item,
            action,
            actor_role=actor_role,
            actor_tenant_id=actor_tenant_id,
            now=now,
            scheduled_for=scheduled_for,
        )
        _raise_if_error(result)
        assert isinstance(result, ContentItem)  # noqa: S101 - narrowed by _raise_if_error
        moved = await self._repo.compare_and_set_status(
            item.id,
            expected=item.status,
            new=result.status,
            scheduled_for=result.scheduled_for,
            updated_at=now,
        )
        if not moved:
            raise InvalidTransitionError(item.status, action.value, "item changed concurrently")
        if action is Action.SCHEDULE and page is not None:
            # B2 (ج): the job rides on the same session/transaction as the CAS above — if the
            # insert fails (``uq_publish_jobs_active_item``) nothing of this request commits.
            try:
                await self._jobs.create_job(
                    _job_for(
                        item,
                        result,
                        page,
                        actor_tenant_id,
                        actor_user_id,
                        now,
                        max_attempts=self._publish_max_attempts,
                    )
                )
            except ActiveJobExistsError as exc:
                raise PublishInProgressError() from exc
        event_action = _EVENT_FOR_ACTION.get(action)
        if action in _CANCELLING_ACTIONS and item.status is ContentStatus.SCHEDULED:
            event_action = ApprovalAction.PUBLISH_CANCELLED
        if event_action is not None and item.current_revision_id is not None:
            await self._repo.add_event(
                ApprovalEvent(
                    item_id=item.id,
                    revision_id=item.current_revision_id,
                    action=event_action,
                    actor_tenant_id=actor_tenant_id,
                    actor_user_id=actor_user_id,
                    note=note,
                    created_at=now,
                )
            )
        logger.info(
            "content transition: brand=%s item=%s %s --%s--> %s by role=%s",
            brand_id,
            item.id,
            item.status.value,
            action.value,
            result.status.value,
            actor_role.value,
        )
        return await self.get_item(brand_id, item_id)

    # ---------------------------------------------------------- B5 generate ---
    async def generate(
        self,
        brand_id: UUID,
        item_id: UUID,
        *,
        gateway: AIGateway,
        limiter: RateLimiter,
        brief: str,
        tone: Tone,
        language: Language,
        actor_tenant_id: UUID,
    ) -> ContentItem:
        """Ask the gateway for a draft and store it as a **new revision** with
        ``provenance={"kind": "ai_generated", ...}`` (D-055 evidence). State must be ``draft``
        or ``rejected`` (else 409 ``invalid_transition``); the item is ``draft`` afterwards.
        20 generations / hour / brand (429 ``generation_rate_limited``) and a daily token
        budget per brand (A-5, 429 ``generation_budget_exceeded``). Usage is logged as a
        structured INFO line (ids + token counts only, never the text) and counted in
        ``counters["ai_completions"]`` / ``["ai_failures"]``. A-3: the item row is locked
        before the revision is numbered."""
        item = await self._lock_item(brand_id, item_id)
        if item.status not in GENERATION_STATES:
            raise InvalidTransitionError(item.status, "generate")
        retry_after = limiter.hit(
            GENERATION_BUCKET, str(brand_id), GENERATION_LIMIT_PER_HOUR, GENERATION_WINDOW_SECONDS
        )
        if retry_after is not None:
            raise GenerationRateLimitedError(retry_after)
        await self._check_token_budget(brand_id)
        system = system_prompt(item.channel, tone=tone, language=language)
        user = user_prompt(brief)
        request = CompletionRequest(
            purpose="content_draft",
            system=system,
            user=user,
            max_tokens=min(2000, channel_definition(item.channel).limits // 2),
            temperature=0.7,
            brand_id=brand_id,
            tenant_id=actor_tenant_id,
        )
        try:
            result = await gateway.complete(request)
        except Exception:
            counters.increment("ai_failures")
            raise
        counters.increment("ai_completions")
        logger.info(
            "ai completion: brand=%s tenant=%s item=%s provider=%s model=%s prompt_tokens=%s "
            "completion_tokens=%s latency_ms=%d",
            brand_id,
            actor_tenant_id,
            item.id,
            result.provider,
            result.model,
            result.prompt_tokens,
            result.completion_tokens,
            result.latency_ms,
        )
        now = datetime.now(tz=UTC)
        text = result.text.strip()
        limit = channel_definition(item.channel).limits
        truncated = len(text) > limit  # A-2: the cut is kept but **recorded** (private field)
        body = text[:limit] or "…"
        provenance: dict[str, Any] = {
            # public (A-1 / A-6)
            "kind": "ai_generated",
            "generated_at": now.isoformat(),
            "prompt_version": PROMPT_VERSION,
            # private — generation_log for OWNER/MANAGER only
            "provider": result.provider,
            "model": result.model,
            "prompt_hash": prompt_hash(system, user),
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "latency_ms": result.latency_ms,
            "credential_mode": result.credential_mode,
            "truncated": truncated,
        }
        if item.status is ContentStatus.REJECTED:
            moved = await self._repo.compare_and_set_status(
                item.id,
                expected=item.status,
                new=ContentStatus.DRAFT,
                scheduled_for=None,
                updated_at=now,
            )
            if not moved:
                raise InvalidTransitionError(item.status, "generate", "item changed concurrently")
        await self._repo.add_revision(
            item.id,
            body=body,
            provenance=provenance,
            created_by_tenant_id=actor_tenant_id,
            created_at=now,
        )
        return await self.get_item(brand_id, item_id)

    async def _check_token_budget(self, brand_id: UUID) -> None:
        """A-5: one JSONB aggregate per generation; ``used >= budget`` ⇒ 429."""
        now = datetime.now(tz=UTC)
        since = now - TOKEN_BUDGET_WINDOW
        used = await self._repo.ai_token_usage_since(brand_id, since)
        if used < self._daily_token_budget:
            return
        oldest = await self._repo.oldest_ai_generation_at(brand_id, since)
        resets_in = (
            max(int((oldest + TOKEN_BUDGET_WINDOW - now).total_seconds()), 1)
            if oldest is not None
            else int(TOKEN_BUDGET_WINDOW.total_seconds())
        )
        raise GenerationBudgetExceededError(self._daily_token_budget, used, resets_in)

    async def _validate_schedule(
        self,
        item: ContentItem,
        scheduled_for: datetime | None,
        page_id: str | None,
        now: datetime,
    ) -> PageAsset:
        """SPRINT-06 B2-U4 rules, then SPRINT-07 B2 (أ) verified publishing scopes ⇒ 409
        ``reauth_required`` and (ب) Instagram needs an https jpg/jpeg/png in
        ``media_refs[0].url`` ⇒ 422 ``instagram_requires_image``."""
        if scheduled_for is None or scheduled_for <= now + SCHEDULE_MIN_LEAD:
            raise ScheduleTooSoonError()
        return await self._validate_target(item, page_id)

    async def _validate_target(self, item: ContentItem, page_id: str | None) -> PageAsset:
        """Target checks shared by ``schedule`` and ``publish/retry`` (B7).

        SPRINT-08 B-1: a channel that is not activated is refused **first** (422
        ``channel_not_activated`` with its incomplete requirements) — before any
        connection / page / scope check, so no brand data is consulted for it.
        """
        definition = channel_definition(item.channel)
        if not definition.is_active:
            raise ChannelNotActivatedError(item.channel, definition.incomplete_requirements())
        if page_id is None:
            raise TargetPageNotSelectedError()
        accounts = await self._connections.list_by_brand(item.brand_id)
        page: PageAsset | None = None
        owner: ConnectedAccount | None = None
        for account in accounts:
            if account.status is not AccountStatus.ACTIVE:
                continue
            for candidate in account.pages:
                if (
                    candidate.page_id == page_id
                    and candidate.is_selected
                    and candidate.encrypted_page_token
                ):
                    page, owner = candidate, account
        if page is None or owner is None:
            raise TargetPageNotSelectedError()
        if item.channel is Channel.INSTAGRAM and page.instagram is None:
            raise InstagramNotLinkedError()
        missing = missing_publish_scopes(owner, definition)
        if missing:
            raise ReauthRequiredError(missing)
        if definition.capabilities.requires_media:
            # SPRINT-07 B2 (ب) via the registry rule: Instagram ⇒ https jpg/jpeg/png image.
            revisions = await self._repo.list_revisions(item.id, limit=1)
            refs = revisions[0].media_refs if revisions else []
            try:
                definition.validate_media(refs)
            except MediaRuleError as exc:
                raise InstagramRequiresImageError() from exc
        return page


def _job_for(
    item: ContentItem,
    scheduled: ContentItem,
    page: PageAsset,
    actor_tenant_id: UUID,
    actor_user_id: UUID,
    now: datetime,
    *,
    max_attempts: int,
) -> PublishJob:
    """The ``queued`` job for a freshly scheduled item (B2: ``run_at = scheduled_for``,
    ``revision_id = current_revision_id``; D-055: who authorised it; SPRINT-08 A-3:
    ``max_attempts`` is the ``PUBLISH_MAX_ATTEMPTS`` policy **at creation** — the row is the
    truth afterwards, the scheduler never re-reads the setting)."""
    assert scheduled.scheduled_for is not None  # noqa: S101 - guaranteed by transition()
    assert item.current_revision_id is not None  # noqa: S101 - approved ⇒ has a revision
    return PublishJob(
        item_id=item.id,
        revision_id=item.current_revision_id,
        brand_id=item.brand_id,
        channel=item.channel.value,
        target_page_id=page.page_id,
        run_at=scheduled.scheduled_for,
        scheduled_by_tenant_id=actor_tenant_id,
        scheduled_by_user_id=actor_user_id,
        max_attempts=max_attempts,
        created_at=now,
        updated_at=now,
    )


def _raise_if_error(result: ContentItem | LifecycleError) -> None:
    if isinstance(result, LifecycleError):
        if result.error == "approval_requires_owner":
            raise ApprovalRequiresOwnerError(result.message)
        if result.error == "insufficient_role":
            raise ContentInsufficientRoleError(result.message)
        raise InvalidTransitionError(result.from_status, result.action.value, result.message)
