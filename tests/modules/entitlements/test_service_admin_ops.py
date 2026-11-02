"""SPRINT-11.1 A-6 (F1) — DB-less coverage for the two admin service operations that only
the PG integration suite exercised: ``set_limits_override`` and ``suspend`` (D-073)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from src.modules.entitlements.domain.models import DEFAULT_LIMITS, Plan, PlanLimits
from src.modules.entitlements.service import BrandNotFoundError, EntitlementService

from tests.conftest import BRAND_ID, OWNER_USER_ID, seed_world
from tests.fakes import FakeEntitlementRepository, FakeStore

LIMITS = PlanLimits(
    daily_tokens=777, monthly_posts=3, monthly_wa_conversations=4, max_brands=1, seats=1
)


async def test_set_limits_override_locks_upserts_and_logs_event(store: FakeStore) -> None:
    seed_world(store)
    repo = FakeEntitlementRepository(store)
    service = EntitlementService(repo)
    updated = await service.set_limits_override(BRAND_ID, LIMITS, actor_user_id=OWNER_USER_ID)
    assert repo.locks == [BRAND_ID]
    assert updated.limits == LIMITS and updated.plan is Plan.TRIAL  # implicit trial row born
    assert repo.entitlements[BRAND_ID].limits == LIMITS
    (event,) = repo.events
    assert event.kind == "limits_overridden" and event.actor_user_id == OWNER_USER_ID
    assert event.payload == {"limits": LIMITS.model_dump()}
    # A stored plan keeps its plan and gets the override.
    await service.activate(BRAND_ID, Plan.GROWTH, 1, actor_user_id=OWNER_USER_ID)
    again = await service.set_limits_override(BRAND_ID, LIMITS, actor_user_id=OWNER_USER_ID)
    assert again.plan is Plan.GROWTH and again.limits == LIMITS
    assert again.limits != DEFAULT_LIMITS[Plan.GROWTH]


async def test_suspend_marks_inactive_and_trims_reason(store: FakeStore) -> None:
    seed_world(store)
    repo = FakeEntitlementRepository(store)
    service = EntitlementService(repo)
    await service.activate(BRAND_ID, Plan.STARTER, 1, actor_user_id=OWNER_USER_ID)
    suspended = await service.suspend(BRAND_ID, actor_user_id=OWNER_USER_ID, reason="x" * 300)
    assert suspended.is_active is False and suspended.plan is Plan.STARTER
    assert repo.entitlements[BRAND_ID].is_active is False
    assert [e.kind for e in repo.events] == ["activated", "suspended"]
    assert repo.events[-1].payload == {"reason": "x" * 256}
    bare = await service.suspend(BRAND_ID, actor_user_id=OWNER_USER_ID)
    assert bare.is_active is False and repo.events[-1].payload == {"reason": ""}


async def test_admin_ops_on_unknown_brand_are_404(store: FakeStore) -> None:
    service = EntitlementService(FakeEntitlementRepository(store))
    with pytest.raises(BrandNotFoundError):
        await service.set_limits_override(uuid4(), LIMITS, actor_user_id=OWNER_USER_ID)
    with pytest.raises(BrandNotFoundError):
        await service.suspend(uuid4(), actor_user_id=OWNER_USER_ID)
