"""SPRINT-11 C-1 / SPRINT-11.1 A-2 (E2c) — the brand's plan ``daily_tokens`` *replace*
``ai_daily_token_budget_per_brand`` inside the ``BudgetGuard``: a stored active plan governs
with its own limits, a brand **without a row is governed by the implicit ``trial``** (20,000 —
what ``GET /entitlement`` shows is what is enforced), and **only** the kill switch falls back to
the global setting. A stored but inactive row ⇒ ``plan_inactive`` (never silently trial)."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from src.modules.ai_gateway.budget import DailyBudget
from src.modules.ai_gateway.budget_guard import (
    BrandBudgetExceededError,
    BudgetGuard,
    EntitlementLimitError,
)
from src.modules.ai_gateway.facades import FakeProvider
from src.modules.ai_gateway.gateway import CompletionRequest
from src.modules.ai_gateway.pricing import PriceTable
from src.modules.entitlements.domain.models import (
    DEFAULT_LIMITS,
    BrandEntitlement,
    Plan,
    PlanLimits,
)
from src.modules.entitlements.service import PgEntitlementLimitsReader

from tests.conftest import BRAND_ID, OWNER_ID
from tests.fakes import FakeCostLogWriter, FakeEntitlementRepository, FakeStore


class _Usage:
    def __init__(self, used: int) -> None:
        self.used = used

    async def combined_usage_since(self, brand_id, since) -> int:  # noqa: ANN001
        return self.used

    async def oldest_generation_since(self, brand_id, since):  # noqa: ANN001, ANN202
        return None


def _req() -> CompletionRequest:
    return CompletionRequest(
        purpose="content_plan",
        system="s",
        user="u",
        brand_id=BRAND_ID,
        tenant_id=OWNER_ID,
        prompt_version="content_plan_v1",
    )


def _guard(reader: PgEntitlementLimitsReader | None, usage: _Usage, setting: int) -> BudgetGuard:
    return BudgetGuard(
        FakeProvider(),
        writer=FakeCostLogWriter(),
        price_table=PriceTable({"fake-1": (Decimal("0"), Decimal("0"))}),
        daily_budget=DailyBudget(setting),
        platform_daily_usd_cap=Decimal("25"),
        usage_reader=usage,
        limits_reader=reader,
    )


def _repo(store: FakeStore, *, daily_tokens: int | None) -> FakeEntitlementRepository:
    repo = FakeEntitlementRepository(store)
    if daily_tokens is not None:
        repo.entitlements[BRAND_ID] = BrandEntitlement(
            brand_id=BRAND_ID,
            plan=Plan.GROWTH,
            limits=PlanLimits(
                daily_tokens=daily_tokens,
                monthly_posts=1,
                monthly_wa_conversations=1,
                max_brands=1,
                seats=1,
            ),
            is_active=True,
        )
    return repo


async def test_stored_plan_daily_tokens_replace_the_global_setting(store: FakeStore) -> None:
    """used=500 > setting=100 but < plan=1000 ⇒ allowed (plan governs, setting ignored)."""
    usage = _Usage(500)
    reader = PgEntitlementLimitsReader(_repo(store, daily_tokens=1000), usage_reader=usage)
    assert await reader.check(BRAND_ID, "content_plan") is True
    result = await _guard(reader, usage, setting=100).complete(_req())
    assert result.provider == "fake"
    # And the plan is the one that refuses once its own ceiling is reached.
    usage.used = 1000
    with pytest.raises(EntitlementLimitError) as info:
        await _guard(reader, usage, setting=10**9).complete(_req())
    assert info.value.limit_kind == "plan_daily_tokens"
    assert info.value.budget == 1000 and info.value.used == 1000


async def test_brand_without_row_is_governed_by_trial_limits(store: FakeStore) -> None:
    """A-2 (E2c): implicit ``trial`` (no row) ⇒ ``check`` returns True and the trial
    ``daily_tokens`` (20,000) govern — the global setting is ignored in both directions."""
    usage = _Usage(500)
    reader = PgEntitlementLimitsReader(_repo(store, daily_tokens=None), usage_reader=usage)
    assert await reader.check(BRAND_ID, "content_plan") is True
    # setting=100 would refuse; the trial plan (20,000) allows ⇒ ok.
    await _guard(reader, usage, setting=100).complete(_req())
    # At the trial ceiling the plan refuses even though the setting (10**9) would allow.
    over = _Usage(DEFAULT_LIMITS[Plan.TRIAL].daily_tokens)
    reader = PgEntitlementLimitsReader(_repo(store, daily_tokens=None), usage_reader=over)
    with pytest.raises(EntitlementLimitError) as info:
        await _guard(reader, over, setting=10**9).complete(_req())
    assert info.value.limit_kind == "plan_daily_tokens"
    assert info.value.budget == 20_000
    assert info.value.used == 20_000


async def test_stored_inactive_row_raises_plan_inactive_not_trial(store: FakeStore) -> None:
    """A-2: a suspended / expired stored plan is refused with ``plan_inactive`` — it never
    degrades to the trial limits (the plan is what the customer bought)."""
    usage = _Usage(0)
    repo = _repo(store, daily_tokens=1000)
    repo.entitlements[BRAND_ID] = repo.entitlements[BRAND_ID].model_copy(
        update={"is_active": False}
    )
    reader = PgEntitlementLimitsReader(repo, usage_reader=usage)
    with pytest.raises(EntitlementLimitError) as info:
        await _guard(reader, usage, setting=10**9).complete(_req())
    assert info.value.limit_kind == "plan_inactive"


async def test_kill_switch_and_no_reader_fall_back_to_the_setting(store: FakeStore) -> None:
    usage = _Usage(500)
    off = PgEntitlementLimitsReader(
        _repo(store, daily_tokens=10), usage_reader=usage, enforced=False
    )
    assert await off.check(BRAND_ID, "content_plan") is False
    await _guard(off, usage, setting=1000).complete(_req())  # plan=10 ignored under the switch
    with pytest.raises(BrandBudgetExceededError):
        await _guard(None, usage, setting=100).complete(_req())
    # Under the switch even an unknown brand (implicit trial) is handed to the setting.
    assert await off.check(uuid4(), "content_plan") is False
