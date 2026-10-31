"""SPRINT-06 B2 / SPRINT-07 B2 — pure lifecycle table (no I/O), 8 states × 7 actions."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from src.modules.content.domain.lifecycle import (
    EDIT_FORBIDDEN,
    EDIT_RESETS_TO_DRAFT,
    SYSTEM_TRANSITIONS,
    TRANSITIONS,
    Action,
    LifecycleError,
    allowed_roles,
    transition,
)
from src.modules.content.domain.models import Channel, ContentItem, ContentStatus
from src.modules.tenancy.domain.access import BrandRole

from tests.conftest import AGENCY_ID, BRAND_ID, OWNER_ID

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
S = ContentStatus
A = Action

# The expected 56-cell matrix, written out explicitly (not derived from the code under test).
EXPECTED: dict[tuple[ContentStatus, Action], ContentStatus | None] = {
    (S.DRAFT, A.SUBMIT): S.PENDING_APPROVAL,
    (S.DRAFT, A.WITHDRAW): None,
    (S.DRAFT, A.APPROVE): None,
    (S.DRAFT, A.REJECT): None,
    (S.DRAFT, A.SCHEDULE): None,
    (S.DRAFT, A.UNSCHEDULE): None,
    (S.DRAFT, A.ARCHIVE): S.ARCHIVED,
    (S.PENDING_APPROVAL, A.SUBMIT): None,
    (S.PENDING_APPROVAL, A.WITHDRAW): S.DRAFT,
    (S.PENDING_APPROVAL, A.APPROVE): S.APPROVED,
    (S.PENDING_APPROVAL, A.REJECT): S.REJECTED,
    (S.PENDING_APPROVAL, A.SCHEDULE): None,
    (S.PENDING_APPROVAL, A.UNSCHEDULE): None,
    (S.PENDING_APPROVAL, A.ARCHIVE): S.ARCHIVED,
    (S.APPROVED, A.SUBMIT): None,
    (S.APPROVED, A.WITHDRAW): None,
    (S.APPROVED, A.APPROVE): None,
    (S.APPROVED, A.REJECT): None,
    (S.APPROVED, A.SCHEDULE): S.SCHEDULED,
    (S.APPROVED, A.UNSCHEDULE): None,
    (S.APPROVED, A.ARCHIVE): S.ARCHIVED,
    (S.REJECTED, A.SUBMIT): None,
    (S.REJECTED, A.WITHDRAW): None,
    (S.REJECTED, A.APPROVE): None,
    (S.REJECTED, A.REJECT): None,
    (S.REJECTED, A.SCHEDULE): None,
    (S.REJECTED, A.UNSCHEDULE): None,
    (S.REJECTED, A.ARCHIVE): S.ARCHIVED,
    (S.SCHEDULED, A.SUBMIT): None,
    (S.SCHEDULED, A.WITHDRAW): None,
    (S.SCHEDULED, A.APPROVE): None,
    (S.SCHEDULED, A.REJECT): None,
    (S.SCHEDULED, A.SCHEDULE): None,
    (S.SCHEDULED, A.UNSCHEDULE): S.APPROVED,
    (S.SCHEDULED, A.ARCHIVE): S.ARCHIVED,
    # SPRINT-07 B2: worker-owned states. ``published`` is final except for archiving;
    # ``publish_failed`` can be unscheduled back to ``approved`` or archived.
    (S.PUBLISHED, A.SUBMIT): None,
    (S.PUBLISHED, A.WITHDRAW): None,
    (S.PUBLISHED, A.APPROVE): None,
    (S.PUBLISHED, A.REJECT): None,
    (S.PUBLISHED, A.SCHEDULE): None,
    (S.PUBLISHED, A.UNSCHEDULE): None,
    (S.PUBLISHED, A.ARCHIVE): S.ARCHIVED,
    (S.PUBLISH_FAILED, A.SUBMIT): None,
    (S.PUBLISH_FAILED, A.WITHDRAW): None,
    (S.PUBLISH_FAILED, A.APPROVE): None,
    (S.PUBLISH_FAILED, A.REJECT): None,
    (S.PUBLISH_FAILED, A.SCHEDULE): None,
    (S.PUBLISH_FAILED, A.UNSCHEDULE): S.APPROVED,
    (S.PUBLISH_FAILED, A.ARCHIVE): S.ARCHIVED,
    (S.ARCHIVED, A.SUBMIT): None,
    (S.ARCHIVED, A.WITHDRAW): None,
    (S.ARCHIVED, A.APPROVE): None,
    (S.ARCHIVED, A.REJECT): None,
    (S.ARCHIVED, A.SCHEDULE): None,
    (S.ARCHIVED, A.UNSCHEDULE): None,
    (S.ARCHIVED, A.ARCHIVE): None,
}
CELLS = sorted(EXPECTED, key=lambda k: (k[0].value, k[1].value))
CELL_IDS = [f"{s.value}--{a.value}" for s, a in CELLS]


def _item(status: ContentStatus, *, created_by=OWNER_ID) -> ContentItem:  # noqa: ANN001
    return ContentItem(
        brand_id=BRAND_ID,
        created_by_tenant_id=created_by,
        channel=Channel.FACEBOOK_PAGE,
        status=status,
        scheduled_for=NOW if status is S.SCHEDULED else None,
        published_at=NOW if status is S.PUBLISHED else None,
        current_revision_id=uuid4(),
    )


def test_b2_u1_matrix_is_complete_and_matches_the_spec() -> None:
    assert len(EXPECTED) == 56 == len(ContentStatus) * len(Action)
    assert set(TRANSITIONS) == set(EXPECTED)
    assert TRANSITIONS == EXPECTED


def test_b2_u1_system_transitions_and_edit_sets() -> None:
    # Only the worker moves an item into the two new states (never a user action).
    assert SYSTEM_TRANSITIONS == {(S.SCHEDULED, S.PUBLISHED), (S.SCHEDULED, S.PUBLISH_FAILED)}
    assert not any(target in {S.PUBLISHED, S.PUBLISH_FAILED} for target in TRANSITIONS.values())
    assert S.PUBLISH_FAILED in EDIT_RESETS_TO_DRAFT
    assert EDIT_FORBIDDEN == {S.ARCHIVED, S.PUBLISHED}


# B2-U1 — all 56 cells, named "<state>--<action>", driven as OWNER (no role interference).
@pytest.mark.parametrize(("state", "action"), CELLS, ids=CELL_IDS)
def test_b2_u1_cell(state: ContentStatus, action: Action) -> None:
    item = _item(state)
    result = transition(
        item,
        action,
        actor_role=BrandRole.OWNER,
        actor_tenant_id=OWNER_ID,
        now=NOW,
        scheduled_for=NOW if action is A.SCHEDULE else None,
    )
    expected = EXPECTED[(state, action)]
    if expected is None:
        assert isinstance(result, LifecycleError)
        assert result.error == "invalid_transition"
        assert (result.from_status, result.action) == (state, action)
    else:
        assert isinstance(result, ContentItem)
        assert result.status is expected
        assert result.updated_at == NOW
        assert (result.scheduled_for is not None) == (expected is S.SCHEDULED)
        # ``published_at`` survives only published → archived (evidence stays with the row).
        keep_published_at = state is S.PUBLISHED and expected is S.ARCHIVED
        assert (result.published_at is not None) == keep_published_at
        # Nothing else moved.
        skip = {"status", "updated_at", "scheduled_for", "published_at"}
        assert result.model_dump(exclude=skip) == item.model_dump(exclude=skip)


# B2-U3 — approve/reject are OWNER only; MANAGER ⇒ approval_requires_owner (even when the
# state would allow it); VIEWER ⇒ insufficient_role for everything.
@pytest.mark.parametrize("action", [A.APPROVE, A.REJECT], ids=["approve", "reject"])
def test_b2_u3_manager_cannot_approve_or_reject(action: Action) -> None:
    result = transition(
        _item(S.PENDING_APPROVAL),
        action,
        actor_role=BrandRole.MANAGER,
        actor_tenant_id=AGENCY_ID,
        now=NOW,
    )
    assert isinstance(result, LifecycleError)
    assert result.error == "approval_requires_owner"
    assert allowed_roles(action) == frozenset({BrandRole.OWNER})


@pytest.mark.parametrize("action", list(Action), ids=[a.value for a in Action])
def test_b2_u3_viewer_is_refused_everything(action: Action) -> None:
    result = transition(
        _item(S.PENDING_APPROVAL),
        action,
        actor_role=BrandRole.VIEWER,
        actor_tenant_id=AGENCY_ID,
        now=NOW,
    )
    assert isinstance(result, LifecycleError)
    assert result.error in {"insufficient_role", "approval_requires_owner"}


def test_b2_u3_owner_may_approve_own_draft() -> None:
    result = transition(
        _item(S.PENDING_APPROVAL, created_by=OWNER_ID),
        A.APPROVE,
        actor_role=BrandRole.OWNER,
        actor_tenant_id=OWNER_ID,
        now=NOW,
    )
    assert isinstance(result, ContentItem) and result.status is S.APPROVED


def test_b2_manager_archives_only_own_items() -> None:
    own = transition(
        _item(S.DRAFT, created_by=AGENCY_ID),
        A.ARCHIVE,
        actor_role=BrandRole.MANAGER,
        actor_tenant_id=AGENCY_ID,
        now=NOW,
    )
    assert isinstance(own, ContentItem) and own.status is S.ARCHIVED
    foreign = transition(
        _item(S.DRAFT, created_by=OWNER_ID),
        A.ARCHIVE,
        actor_role=BrandRole.MANAGER,
        actor_tenant_id=AGENCY_ID,
        now=NOW,
    )
    assert isinstance(foreign, LifecycleError) and foreign.error == "insufficient_role"
    # The owner archives anything.
    any_ = transition(
        _item(S.SCHEDULED, created_by=AGENCY_ID),
        A.ARCHIVE,
        actor_role=BrandRole.OWNER,
        actor_tenant_id=OWNER_ID,
        now=NOW,
    )
    assert isinstance(any_, ContentItem) and any_.scheduled_for is None


# Service-level guard (the HTTP schema already enforces ≥3 chars; the service must too).
async def test_b2_u3_service_reject_requires_note_after_role_check() -> None:
    from src.modules.content.exceptions import (
        ApprovalRequiresOwnerError,
        RejectNoteRequiredError,
    )
    from src.modules.content.service import ContentService

    from tests.fakes import (
        FakeConnectedAccountRepository,
        FakeContentRepository,
        FakePublishJobRepository,
        FakeStore,
    )

    repo = FakeContentRepository()
    service = ContentService(
        repo, FakeConnectedAccountRepository(FakeStore()), FakePublishJobRepository()
    )
    item = await service.create_item(
        BRAND_ID,
        actor_tenant_id=OWNER_ID,
        channel=Channel.FACEBOOK_PAGE,
        body="نص",
        target_page_id=None,
    )
    repo.items[item.id] = repo.items[item.id].model_copy(update={"status": S.PENDING_APPROVAL})
    with pytest.raises(RejectNoteRequiredError):
        await service.act(
            BRAND_ID,
            item.id,
            A.REJECT,
            actor_role=BrandRole.OWNER,
            actor_tenant_id=OWNER_ID,
            actor_user_id=uuid4(),
            note="  ",
        )
    with pytest.raises(ApprovalRequiresOwnerError):  # role is checked before the note rule
        await service.act(
            BRAND_ID,
            item.id,
            A.REJECT,
            actor_role=BrandRole.MANAGER,
            actor_tenant_id=AGENCY_ID,
            actor_user_id=uuid4(),
            note=None,
        )
    assert repo.items[item.id].status is S.PENDING_APPROVAL
