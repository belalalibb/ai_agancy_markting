"""SPRINT-11.1 A-6 (F1) — unit coverage for the DB-less-testable lines the SPRINT-11 review
listed as missing in the ``pytest -m "not integration"`` CI gate: pure factories, small
helpers and error branches. No ``# pragma: no cover`` is added anywhere (§5-2)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from src.modules.content.domain.models import Channel  # noqa: F401 - import order (circular)


class _Session:
    """Stand-in for an ``AsyncSession`` — the factories only store it."""


# ------------------------------------------------------------- factories (no DB) ----
def test_ai_gateway_entitlement_limits_reader_factory_builds_pg_reader(settings) -> None:  # noqa: ANN001
    from src.modules.ai_gateway.dependencies import get_entitlement_limits_reader
    from src.modules.entitlements.service import PgEntitlementLimitsReader

    class Usage:
        async def combined_usage_since(self, brand_id, since):  # noqa: ANN001, ANN202
            return 0

        async def oldest_generation_since(self, brand_id, since):  # noqa: ANN001, ANN202
            return None

    reader = get_entitlement_limits_reader(_Session(), Usage(), settings)  # type: ignore[arg-type]
    assert isinstance(reader, PgEntitlementLimitsReader)
    assert reader._enforced is settings.feature_entitlements_enforced  # noqa: SLF001
    off = settings.model_copy(update={"feature_entitlements_enforced": False})
    assert get_entitlement_limits_reader(_Session(), Usage(), off)._enforced is False  # type: ignore[arg-type]  # noqa: SLF001


def test_tenancy_consent_reader_factories_share_one_composition_root() -> None:
    from src.modules.tenancy.dependencies import (
        build_brand_consent_reader,
        get_brand_consent_reader,
    )
    from src.modules.tenancy.infrastructure.postgres_repository import PostgresBrandConsentReader

    session = _Session()
    built = build_brand_consent_reader(session)  # type: ignore[arg-type]
    injected = get_brand_consent_reader(session)  # type: ignore[arg-type]
    assert isinstance(built, PostgresBrandConsentReader)
    assert type(injected) is type(built)


def test_entitlements_dependency_factories_build_pg_adapters(settings) -> None:  # noqa: ANN001
    from src.modules.entitlements import dependencies as deps
    from src.modules.entitlements.infrastructure.postgres_repository import (
        PgCounterUsageReader,
        PgEntitlementRepository,
    )
    from src.modules.entitlements.service import EntitlementService

    session = _Session()
    repo = deps.get_entitlement_repository(session)  # type: ignore[arg-type]
    assert isinstance(repo, PgEntitlementRepository)
    assert isinstance(deps.get_counter_usage_reader(session), PgCounterUsageReader)  # type: ignore[arg-type]
    assert isinstance(deps.get_entitlement_service(repo), EntitlementService)


async def test_plan_counter_guard_re_raises_non_counter_limit_errors() -> None:
    """A-2 adapter: only the *matching* counter is translated into the consumer's 402 class;
    ``plan_inactive`` keeps its 429 contract and propagates unchanged."""
    from src.modules.ai_gateway.budget_guard import EntitlementLimitError
    from src.modules.entitlements.dependencies import PlanCounterGuard, counter_guard_dependency

    class ExceededError(Exception):
        def __init__(self, counter, *, limit, used) -> None:  # noqa: ANN001
            super().__init__(counter)
            self.info = (counter, limit, used)

    class Limits:
        def __init__(self, error: EntitlementLimitError | None) -> None:
            self.error = error
            self.calls: list[tuple] = []

        async def check_counter(self, brand_id, counter, *, reserve):  # noqa: ANN001, ANN202
            self.calls.append((counter, reserve))
            if self.error is not None:
                raise self.error

    brand = uuid4()
    inactive = Limits(EntitlementLimitError("plan_inactive"))
    with pytest.raises(EntitlementLimitError) as info:
        await PlanCounterGuard(inactive, exceeded=ExceededError).ensure_can_approve_post(brand)  # type: ignore[arg-type]
    assert info.value.limit_kind == "plan_inactive"
    assert inactive.calls == [("monthly_posts", 1)]

    over = Limits(EntitlementLimitError("monthly_wa_conversations", budget=50, used=51))
    with pytest.raises(ExceededError) as ex:
        await PlanCounterGuard(over, exceeded=ExceededError).ensure_within_conversation_quota(
            brand
        )  # type: ignore[arg-type]
    assert ex.value.info == ("monthly_wa_conversations", 50, 51)
    assert over.calls == [("monthly_wa_conversations", 0)]

    ok = Limits(None)
    await PlanCounterGuard(ok, exceeded=ExceededError).ensure_can_approve_post(brand)  # type: ignore[arg-type]
    # ``budget``/``used`` default to 0 when the limit error carries none.
    bare = Limits(EntitlementLimitError("monthly_posts"))
    with pytest.raises(ExceededError) as ex:
        await PlanCounterGuard(bare, exceeded=ExceededError).ensure_can_approve_post(brand)  # type: ignore[arg-type]
    assert ex.value.info == ("monthly_posts", 0, 0)
    dependency = counter_guard_dependency(ExceededError)
    assert isinstance(dependency(ok), PlanCounterGuard)  # type: ignore[arg-type]


# ------------------------------------------------------------------- helpers ----
def test_whatsapp_retry_after_header_parsing() -> None:
    from src.modules.whatsapp.client import _retry_after

    assert _retry_after(httpx.Response(429)) is None
    assert _retry_after(httpx.Response(429, headers={"Retry-After": "12.5"})) == 12.5
    assert _retry_after(httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct"})) is None


def test_channel_registry_unknown_channel_paths() -> None:
    from src.modules.publishing.channels import get_registry
    from src.modules.publishing.channels.registry import ChannelRegistry, UnknownChannelError

    registry = get_registry()
    with pytest.raises(UnknownChannelError) as info:
        registry.get("myspace")  # not a Channel value
    assert info.value.channel == "myspace" and "myspace" in str(info.value)
    # A registry missing a definition for a *valid* enum value hits the KeyError branch.
    partial = ChannelRegistry((registry.get(Channel.FACEBOOK_PAGE),))
    with pytest.raises(UnknownChannelError):
        partial.get(Channel.INSTAGRAM)
    assert partial.all() == (registry.get(Channel.FACEBOOK_PAGE),)


def test_publishing_active_job_error_and_structural_publisher_channel() -> None:
    from src.modules.publishing.channels.structural import StructuralPublisher
    from src.modules.publishing.exceptions import ActiveJobExistsError, PublishingError

    err = ActiveJobExistsError()
    assert isinstance(err, PublishingError) and "active publish job" in str(err)
    assert StructuralPublisher(Channel.TIKTOK).channel is Channel.TIKTOK


def test_price_table_estimate_unit_and_daily_budget_limit() -> None:
    from src.modules.ai_gateway.budget import DailyBudget
    from src.modules.ai_gateway.pricing import PriceTable

    table = PriceTable({"m": (Decimal("0.5"), Decimal("1"))})
    assert table.estimate_unit("m") == (Decimal("0.5"), Decimal("1"))
    assert table.estimate_unit("other") is None
    assert DailyBudget(1234).limit == 1234


def test_whatsapp_client_error_str_without_http_status() -> None:
    from src.modules.whatsapp.exceptions import WhatsAppClientError

    plain = WhatsAppClientError("x")
    assert "http=" not in str(plain)
    with_status = WhatsAppClientError("x")
    with_status.http_status = 503
    assert "http=503" in str(with_status)


def test_secretstr_never_leaks_in_cost_entry_dump() -> None:
    """Sanity for the Zero-Secret rule: a ``SecretStr`` repr never shows the raw value."""
    from src.modules.ai_gateway.cost_log import CostEntry, CostOutcome

    key = SecretStr("sk-UNIT-TEST-NOT-A-REAL-KEY")
    assert "sk-UNIT" not in repr(key) and "sk-UNIT" not in str(key)
    entry = CostEntry(
        brand_id=None,
        purpose="admin_probe",
        provider="fake",
        model="fake-1",
        credential_mode="platform",
        latency_ms=0,
        prompt_version="p",
        outcome=CostOutcome.PENDING,
        created_at=datetime.now(tz=UTC),
    )
    assert entry.brand_id is None and "sk-" not in entry.model_dump_json()
