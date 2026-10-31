"""Explicit content lifecycle — pure, no I/O (SPRINT-06 B2, D-055).

::

    draft ──submit──▶ pending_approval ──approve──▶ approved ──schedule──▶ scheduled
      ▲                    │      │                    ▲                      │
      │◀────withdraw───────┘      └──reject──▶ rejected│◀──────unschedule─────┘
      │                                                 │
      └──────── edit body (any state after draft) ──────┘   (new revision ⇒ draft)

    any non-archived ──archive──▶ archived (final)

    scheduled ══(worker)══▶ published            (SYSTEM_TRANSITIONS — not user actions)
    scheduled ══(worker)══▶ publish_failed ──unschedule──▶ approved   (re-target / re-time)
                                           ──archive────▶ archived
    published ──archive──▶ archived   (local only — the Meta post is **not** deleted)

Every one of the 8 × 7 = 56 (state, action) cells is listed in :data:`TRANSITIONS` — a
missing cell is a programming error, not an implicit "allowed". Role rules (D-055):
``approve`` / ``reject`` are **OWNER only**; everything else is OWNER or MANAGER; ``archive``
additionally requires a MANAGER to be the creator of the item. The two **system**
transitions (``scheduled → published`` / ``scheduled → publish_failed``) live in
:data:`SYSTEM_TRANSITIONS`; only the scheduler worker performs them (CAS), never a request.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from src.modules.content.domain.models import ContentItem, ContentStatus
from src.modules.tenancy.domain.access import BrandRole


class Action(StrEnum):
    SUBMIT = "submit"
    WITHDRAW = "withdraw"
    APPROVE = "approve"
    REJECT = "reject"
    SCHEDULE = "schedule"
    UNSCHEDULE = "unschedule"
    ARCHIVE = "archive"


@dataclass(frozen=True, slots=True)
class LifecycleError:
    """Returned (not raised) by :func:`transition` — the caller maps it to HTTP."""

    error: str  # fixed snake_case code
    from_status: ContentStatus
    action: Action
    message: str


_S = ContentStatus
_A = Action

# (from, action) -> to. ``None`` = invalid transition (409 invalid_transition).
TRANSITIONS: Final[dict[tuple[ContentStatus, Action], ContentStatus | None]] = {
    # draft
    (_S.DRAFT, _A.SUBMIT): _S.PENDING_APPROVAL,
    (_S.DRAFT, _A.WITHDRAW): None,
    (_S.DRAFT, _A.APPROVE): None,
    (_S.DRAFT, _A.REJECT): None,
    (_S.DRAFT, _A.SCHEDULE): None,
    (_S.DRAFT, _A.UNSCHEDULE): None,
    (_S.DRAFT, _A.ARCHIVE): _S.ARCHIVED,
    # pending_approval
    (_S.PENDING_APPROVAL, _A.SUBMIT): None,
    (_S.PENDING_APPROVAL, _A.WITHDRAW): _S.DRAFT,
    (_S.PENDING_APPROVAL, _A.APPROVE): _S.APPROVED,
    (_S.PENDING_APPROVAL, _A.REJECT): _S.REJECTED,
    (_S.PENDING_APPROVAL, _A.SCHEDULE): None,
    (_S.PENDING_APPROVAL, _A.UNSCHEDULE): None,
    (_S.PENDING_APPROVAL, _A.ARCHIVE): _S.ARCHIVED,
    # approved
    (_S.APPROVED, _A.SUBMIT): None,
    (_S.APPROVED, _A.WITHDRAW): None,
    (_S.APPROVED, _A.APPROVE): None,
    (_S.APPROVED, _A.REJECT): None,
    (_S.APPROVED, _A.SCHEDULE): _S.SCHEDULED,
    (_S.APPROVED, _A.UNSCHEDULE): None,
    (_S.APPROVED, _A.ARCHIVE): _S.ARCHIVED,
    # rejected
    (_S.REJECTED, _A.SUBMIT): None,
    (_S.REJECTED, _A.WITHDRAW): None,
    (_S.REJECTED, _A.APPROVE): None,
    (_S.REJECTED, _A.REJECT): None,
    (_S.REJECTED, _A.SCHEDULE): None,
    (_S.REJECTED, _A.UNSCHEDULE): None,
    (_S.REJECTED, _A.ARCHIVE): _S.ARCHIVED,
    # scheduled
    (_S.SCHEDULED, _A.SUBMIT): None,
    (_S.SCHEDULED, _A.WITHDRAW): None,
    (_S.SCHEDULED, _A.APPROVE): None,
    (_S.SCHEDULED, _A.REJECT): None,
    (_S.SCHEDULED, _A.SCHEDULE): None,
    (_S.SCHEDULED, _A.UNSCHEDULE): _S.APPROVED,
    (_S.SCHEDULED, _A.ARCHIVE): _S.ARCHIVED,
    # archived (final)
    (_S.ARCHIVED, _A.SUBMIT): None,
    (_S.ARCHIVED, _A.WITHDRAW): None,
    (_S.ARCHIVED, _A.APPROVE): None,
    (_S.ARCHIVED, _A.REJECT): None,
    (_S.ARCHIVED, _A.SCHEDULE): None,
    (_S.ARCHIVED, _A.UNSCHEDULE): None,
    (_S.ARCHIVED, _A.ARCHIVE): None,
    # published (SPRINT-07 B2) — the post exists on Meta; only a *local* archive is allowed
    (_S.PUBLISHED, _A.SUBMIT): None,
    (_S.PUBLISHED, _A.WITHDRAW): None,
    (_S.PUBLISHED, _A.APPROVE): None,
    (_S.PUBLISHED, _A.REJECT): None,
    (_S.PUBLISHED, _A.SCHEDULE): None,
    (_S.PUBLISHED, _A.UNSCHEDULE): None,
    (_S.PUBLISHED, _A.ARCHIVE): _S.ARCHIVED,
    # publish_failed (SPRINT-07 B2) — back to approved (re-target / re-time) or archive
    (_S.PUBLISH_FAILED, _A.SUBMIT): None,
    (_S.PUBLISH_FAILED, _A.WITHDRAW): None,
    (_S.PUBLISH_FAILED, _A.APPROVE): None,
    (_S.PUBLISH_FAILED, _A.REJECT): None,
    (_S.PUBLISH_FAILED, _A.SCHEDULE): None,
    (_S.PUBLISH_FAILED, _A.UNSCHEDULE): _S.APPROVED,
    (_S.PUBLISH_FAILED, _A.ARCHIVE): _S.ARCHIVED,
}
assert len(TRANSITIONS) == len(ContentStatus) * len(Action) == 56  # noqa: S101 - table integrity

# SPRINT-07 B2: the closed list of transitions the scheduler worker performs by CAS. They are
# **not** user actions, never appear in ``TRANSITIONS`` and have no role check — the
# authorisation happened at ``schedule`` time (D-055).
SYSTEM_TRANSITIONS: Final[frozenset[tuple[ContentStatus, ContentStatus]]] = frozenset(
    {(_S.SCHEDULED, _S.PUBLISHED), (_S.SCHEDULED, _S.PUBLISH_FAILED)}
)
assert len(SYSTEM_TRANSITIONS) == 2  # noqa: S101 - closed list

# Visibility table (directive §2-0 b): who may perform which action.
OWNER_ONLY: Final[frozenset[Action]] = frozenset({Action.APPROVE, Action.REJECT})
OWNER_OR_MANAGER: Final[frozenset[BrandRole]] = frozenset({BrandRole.OWNER, BrandRole.MANAGER})

# States in which an edit of the body produces a new revision *and* returns to draft with an
# automatic ``withdrawn`` event (B2-U2). Editing a plain draft just adds a revision.
EDIT_RESETS_TO_DRAFT: Final[frozenset[ContentStatus]] = frozenset(
    {_S.PENDING_APPROVAL, _S.APPROVED, _S.REJECTED, _S.SCHEDULED, _S.PUBLISH_FAILED}
)
# Editing is impossible once the post exists on Meta or the item is archived (409).
EDIT_FORBIDDEN: Final[frozenset[ContentStatus]] = frozenset({_S.ARCHIVED, _S.PUBLISHED})


def allowed_roles(action: Action) -> frozenset[BrandRole]:
    return frozenset({BrandRole.OWNER}) if action in OWNER_ONLY else OWNER_OR_MANAGER


def transition(
    item: ContentItem,
    action: Action,
    *,
    actor_role: BrandRole,
    actor_tenant_id: UUID,
    now: datetime,
    scheduled_for: datetime | None = None,
) -> ContentItem | LifecycleError:
    """Apply ``action`` to ``item`` or explain why not. Pure function.

    Order of checks: role (403 codes) ⇒ table (409 ``invalid_transition``). ``scheduled_for``
    is only consulted for ``schedule`` (the time-window / target checks live in the service
    because they need the repository).
    """
    if actor_role not in allowed_roles(action):
        if action in OWNER_ONLY:
            return LifecycleError(
                "approval_requires_owner",
                item.status,
                action,
                "Only the brand owner may approve or reject content (D-055).",
            )
        return LifecycleError(
            "insufficient_role", item.status, action, f"role {actor_role.value!r} may not {action}"
        )
    if (
        action is Action.ARCHIVE
        and actor_role is BrandRole.MANAGER
        and item.created_by_tenant_id != actor_tenant_id
    ):
        return LifecycleError(
            "insufficient_role",
            item.status,
            action,
            "A manager may only archive drafts it created itself.",
        )
    target = TRANSITIONS[(item.status, action)]
    if target is None:
        return LifecycleError(
            "invalid_transition",
            item.status,
            action,
            f"cannot {action} an item in status {item.status.value!r}",
        )
    update: dict[str, object] = {"status": target, "updated_at": now}
    if target is ContentStatus.SCHEDULED:
        update["scheduled_for"] = scheduled_for
    else:
        update["scheduled_for"] = None
    # ``published_at`` is set only by the system transition; every user move clears it except
    # the local archive of a published post, which keeps the publication evidence.
    if not (item.status is ContentStatus.PUBLISHED and target is ContentStatus.ARCHIVED):
        update["published_at"] = None
    return item.model_copy(update=update)
